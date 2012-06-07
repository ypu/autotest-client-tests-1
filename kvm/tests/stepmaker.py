#!/usr/bin/python
"""
Step file creator/editor.

@copyright: Red Hat Inc 2009
@author: mgoldish@redhat.com (Michael Goldish)
@version: "20090401"
"""

import pygtk, gtk, gobject, time, os, commands, logging
try:
    import autotest.common as common
except ImportError:
    import common
from autotest.client.shared import error
from autotest.client.virt import virt_utils, ppm_utils, virt_step_editor
from autotest.client.virt import kvm_monitor
pygtk.require('2.0')


class StepMaker(virt_step_editor.StepMakerWindow):
    """
    Application used to create a step file. It will grab your input to the
    virtual machine and record it on a 'step file', that can be played
    making it possible to do unattended installs.
    """
    # Constructor
    def __init__(self, vm, steps_filename, tempdir, params):
        virt_step_editor.StepMakerWindow.__init__(self)

        self.vm = vm
        self.steps_filename = steps_filename
        self.steps_data_dir = ppm_utils.get_data_dir(steps_filename)
        self.tempdir = tempdir
        self.screendump_filename = os.path.join(tempdir, "scrdump.ppm")
        self.params = params

        if not os.path.exists(self.steps_data_dir):
            os.makedirs(self.steps_data_dir)

        self.steps_file = open(self.steps_filename, "w")
        self.vars_file = open(os.path.join(self.steps_data_dir, "vars"), "w")

        self.step_num = 1
        self.run_time = 0
        self.update_delay = 1000
        self.prev_x = 0
        self.prev_y = 0
        self.vars = {}
        self.timer_id = None

        self.time_when_done_clicked = time.time()
        self.time_when_actions_completed = time.time()

        self.steps_file.write("# Generated by Step Maker\n")
        self.steps_file.write("# Generated on %s\n" % time.asctime())
        self.steps_file.write("# uname -a: %s\n" %
                              commands.getoutput("uname -a"))
        self.steps_file.flush()

        self.vars_file.write("# This file lists the vars used during recording"
                             " with Step Maker\n")
        self.vars_file.flush()

        # Done/Break HBox
        hbox = gtk.HBox(spacing=10)
        self.user_vbox.pack_start(hbox)
        hbox.show()

        self.button_break = gtk.Button("Break")
        self.button_break.connect("clicked", self.event_break_clicked)
        hbox.pack_start(self.button_break)
        self.button_break.show()

        self.button_done = gtk.Button("Done")
        self.button_done.connect("clicked", self.event_done_clicked)
        hbox.pack_start(self.button_done)
        self.button_done.show()

        # Set window title
        self.window.set_title("Step Maker")

        # Connect "capture" button
        self.button_capture.connect("clicked", self.event_capture_clicked)

        # Switch to run mode
        self.switch_to_run_mode()


    def destroy(self, widget):
        self.vm.resume()
        self.steps_file.close()
        self.vars_file.close()
        virt_step_editor.StepMakerWindow.destroy(self, widget)


    # Utilities
    def redirect_timer(self, delay=0, func=None):
        if self.timer_id != None:
            gobject.source_remove(self.timer_id)
        self.timer_id = None
        if func != None:
            self.timer_id = gobject.timeout_add(delay, func,
                                                priority=gobject.PRIORITY_LOW)


    def switch_to_run_mode(self):
        # Set all widgets to their default states
        self.clear_state(clear_screendump=False)
        # Enable/disable some widgets
        self.button_break.set_sensitive(True)
        self.button_done.set_sensitive(False)
        self.data_vbox.set_sensitive(False)
        # Give focus to the Break button
        self.button_break.grab_focus()
        # Start the screendump timer
        self.redirect_timer(100, self.update)
        # Resume the VM
        self.vm.resume()


    def switch_to_step_mode(self):
        # Set all widgets to their default states
        self.clear_state(clear_screendump=False)
        # Enable/disable some widgets
        self.button_break.set_sensitive(False)
        self.button_done.set_sensitive(True)
        self.data_vbox.set_sensitive(True)
        # Give focus to the keystrokes entry widget
        self.entry_keys.grab_focus()
        # Start the screendump timer
        self.redirect_timer()
        # Stop the VM
        self.vm.pause()


    # Events in step mode
    def update(self):
        self.redirect_timer()

        if os.path.exists(self.screendump_filename):
            os.unlink(self.screendump_filename)

        try:
            self.vm.monitor.screendump(self.screendump_filename, debug=False)
        except kvm_monitor.MonitorError, e:
            logging.warn(e)
        else:
            self.set_image_from_file(self.screendump_filename)

        self.redirect_timer(self.update_delay, self.update)
        return True


    def event_break_clicked(self, widget):
        if not self.vm.is_alive():
            self.message("The VM doesn't seem to be alive.", "Error")
            return
        # Switch to step mode
        self.switch_to_step_mode()
        # Compute time elapsed since last click on "Done" and add it
        # to self.run_time
        self.run_time += time.time() - self.time_when_done_clicked
        # Set recording time widget
        self.entry_time.set_text("%.2f" % self.run_time)
        # Update screendump ID
        self.update_screendump_id(self.steps_data_dir)
        # By default, check the barrier checkbox
        self.check_barrier.set_active(True)
        # Set default sleep and barrier timeout durations
        time_delta = time.time() - self.time_when_actions_completed
        if time_delta < 1.0: time_delta = 1.0
        self.spin_sleep.set_value(round(time_delta))
        self.spin_barrier_timeout.set_value(round(time_delta * 5))
        # Set window title
        self.window.set_title("Step Maker -- step %d at time %.2f" %
                              (self.step_num, self.run_time))


    def event_done_clicked(self, widget):
        # Get step lines and screendump
        lines = self.get_step_lines(self.steps_data_dir)
        if lines == None:
            return

        # Get var values from user and write them to vars file
        vars = {}
        for line in lines.splitlines():
            words = line.split()
            if words and words[0] == "var":
                varname = words[1]
                if varname in self.vars.keys():
                    val = self.vars[varname]
                elif varname in vars.keys():
                    val = vars[varname]
                elif varname in self.params.keys():
                    val = self.params[varname]
                    vars[varname] = val
                else:
                    val = self.inputdialog("$%s =" % varname, "Variable")
                    if val == None:
                        return
                    vars[varname] = val
        for varname in vars.keys():
            self.vars_file.write("%s=%s\n" % (varname, vars[varname]))
        self.vars.update(vars)

        # Write step lines to file
        self.steps_file.write("# " + "-" * 32 + "\n")
        self.steps_file.write(lines)

        # Flush buffers of both files
        self.steps_file.flush()
        self.vars_file.flush()

        # Remember the current time
        self.time_when_done_clicked = time.time()

        # Switch to run mode
        self.switch_to_run_mode()

        # Send commands to VM
        for line in lines.splitlines():
            words = line.split()
            if not words:
                continue
            elif words[0] == "key":
                self.vm.send_key(words[1])
            elif words[0] == "var":
                val = self.vars.get(words[1])
                if not val:
                    continue
                self.vm.send_string(val)
            elif words[0] == "mousemove":
                self.vm.monitor.mouse_move(-8000, -8000)
                time.sleep(0.5)
                self.vm.monitor.mouse_move(words[1], words[2])
                time.sleep(0.5)
            elif words[0] == "mouseclick":
                self.vm.monitor.mouse_button(words[1])
                time.sleep(0.1)
                self.vm.monitor.mouse_button(0)

        # Remember the current time
        self.time_when_actions_completed = time.time()

        # Move on to next step
        self.step_num += 1

    def event_capture_clicked(self, widget):
        self.message("Mouse actions disabled (for now).", "Sorry")
        return

        self.image_width_backup = self.image_width
        self.image_height_backup = self.image_height
        self.image_data_backup = self.image_data

        gtk.gdk.pointer_grab(self.event_box.window, False,
                             gtk.gdk.BUTTON_PRESS_MASK |
                             gtk.gdk.BUTTON_RELEASE_MASK)
        # Create empty cursor
        pix = gtk.gdk.Pixmap(self.event_box.window, 1, 1, 1)
        color = gtk.gdk.Color()
        cursor = gtk.gdk.Cursor(pix, pix, color, color, 0, 0)
        self.event_box.window.set_cursor(cursor)
        gtk.gdk.display_get_default().warp_pointer(gtk.gdk.screen_get_default(),
                                                   self.prev_x, self.prev_y)
        self.redirect_event_box_input(
                self.event_capture_button_press,
                self.event_capture_button_release,
                self.event_capture_scroll)
        self.redirect_timer(10, self.update_capture)
        self.vm.resume()

    # Events in mouse capture mode

    def update_capture(self):
        self.redirect_timer()

        (screen, x, y, flags) = gtk.gdk.display_get_default().get_pointer()
        self.mouse_click_coords[0] = int(x * self.spin_sensitivity.get_value())
        self.mouse_click_coords[1] = int(y * self.spin_sensitivity.get_value())

        delay = self.spin_latency.get_value() / 1000
        if (x, y) != (self.prev_x, self.prev_y):
            self.vm.monitor.mouse_move(-8000, -8000)
            time.sleep(delay)
            self.vm.monitor.mouse_move(self.mouse_click_coords[0],
                                       self.mouse_click_coords[1])
            time.sleep(delay)

        self.prev_x = x
        self.prev_y = y

        if os.path.exists(self.screendump_filename):
            os.unlink(self.screendump_filename)

        try:
            self.vm.monitor.screendump(self.screendump_filename, debug=False)
        except kvm_monitor.MonitorError, e:
            logging.warn(e)
        else:
            self.set_image_from_file(self.screendump_filename)

        self.redirect_timer(int(self.spin_latency.get_value()),
                            self.update_capture)
        return True

    def event_capture_button_press(self, widget,event):
        pass

    def event_capture_button_release(self, widget,event):
        gtk.gdk.pointer_ungrab()
        self.event_box.window.set_cursor(gtk.gdk.Cursor(gtk.gdk.CROSSHAIR))
        self.redirect_event_box_input(
                self.event_button_press,
                self.event_button_release,
                None,
                None,
                self.event_expose)
        self.redirect_timer()
        self.vm.pause()
        self.mouse_click_captured = True
        self.mouse_click_button = event.button
        self.set_image(self.image_width_backup, self.image_height_backup,
                       self.image_data_backup)
        self.check_mousemove.set_sensitive(True)
        self.check_mouseclick.set_sensitive(True)
        self.check_mousemove.set_active(True)
        self.check_mouseclick.set_active(True)
        self.update_mouse_click_info()

    def event_capture_scroll(self, widget, event):
        if event.direction == gtk.gdk.SCROLL_UP:
            direction = 1
        else:
            direction = -1
        self.spin_sensitivity.set_value(self.spin_sensitivity.get_value() +
                                        direction)
        pass


def run_stepmaker(test, params, env):
    vm = env.get_vm(params.get("main_vm"))
    if not vm:
        raise error.TestError("VM object not found in environment")
    if not vm.is_alive():
        raise error.TestError("VM seems to be dead; Step Maker requires a"
                              " living VM")

    steps_filename = params.get("steps")
    if not steps_filename:
        raise error.TestError("Steps filename not specified")
    steps_filename = virt_utils.get_path(test.virtdir, steps_filename)
    if os.path.exists(steps_filename):
        raise error.TestError("Steps file %s already exists" % steps_filename)

    StepMaker(vm, steps_filename, test.debugdir, params)
    gtk.main()

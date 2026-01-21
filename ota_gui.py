import json
import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from threading import Thread
import time
import logging
import re
import datetime
from tkinter import simpledialog

# Setup logging
logging.basicConfig(filename='ota_editor.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class OTAConfigEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("OTA Config Editor")
        self.root.geometry("800x500")
        self.root.minsize(800, 500)
        self.root.resizable(True, True)
        self.root.configure(bg="#f4f7fa")

        # Apply a modern theme
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Configure styles for modern look
        self.style.configure('TButton', padding=12, relief="flat", background="#007bff", foreground="white", font=("Helvetica", 10, "bold"))
        self.style.map('TButton', background=[('active', '#0056b3')], foreground=[('active', 'white')])

        self.style.configure('TLabel', background="#f4f7fa", foreground="#2c3e50", font=("Helvetica", 10))
        self.style.configure('TEntry', fieldbackground="white", foreground="#2c3e50", font=("Helvetica", 10))
        self.style.configure('TFrame', background="#f4f7fa")
        self.style.configure('TLabelframe', background="white", foreground="#2c3e50", relief="flat", borderwidth=0)
        self.style.configure('TLabelframe.Label', background="white", foreground="#2c3e50", font=("Helvetica", 12, "bold"))

        self.style.configure('Status.TLabel', background="#e9ecef", foreground="#495057", padding=10, font=("Helvetica", 9))

        self.style.configure('Error.TEntry', fieldbackground="#ffcccc")  # For invalid entries

        # Variables for config fields
        self.config = {}
        self.fields = ["ICC_PNO", "VIN", "f1A1", "0525"]
        self.entries = {}
        self.validators = {
            "ICC_PNO": self.validate_icc_pno,
            "VIN": self.validate_vin,
            "f1A1": self.validate_hex,
            "0525": self.validate_hex_short
        }

        # Local file path
        self.local_file = "DeviceInfo.txt"
        self.remote_path = "/mnt/sdcard/DeviceInfo.txt"
        self.backup_dir = "backups"
        os.makedirs(self.backup_dir, exist_ok=True)

        # Root password - Use environment variable or prompt
        self.root_password = os.environ.get('ADB_ROOT_PASSWORD')
        if not self.root_password:
            self.root_password = simpledialog.askstring("Root Password", "Enter ADB root password:", show='*', parent=self.root)
            if not self.root_password:
                messagebox.showerror("Error", "Root password required. Exiting.", parent=self.root)
                self.root.quit()

        # UI Elements
        self.create_ui()

        # Load config if local file exists
        self.load_local_config()

        # Bind resize for responsiveness
        self.root.bind("<Configure>", self.on_resize)

    def create_ui(self):
        # Main frame with padding
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header label
        header = ttk.Label(main_frame, text="OTA Configuration Editor", font=("Helvetica", 16, "bold"), foreground="#007bff")
        header.pack(pady=(0, 20))

        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)

        buttons = [
            ("Pull from Device", self.pull_from_device, "#28a745"),  # Green
            ("Load Local File", self.load_from_file, "#ffc107"),    # Yellow
            ("Save Locally", self.save_locally, "#17a2b8"),         # Teal
            ("Push to Device", self.push_to_device, "#dc3545"),     # Red
            ("Restore Backup", self.restore_backup, "#6f42c1")      # Purple for restore
        ]

        for text, command, color in buttons:
            self.style.configure(f'{text}.TButton', background=color, foreground="white")
            self.style.map(f'{text}.TButton', background=[('active', self.darken_color(color))])
            btn = ttk.Button(button_frame, text=text, command=command, style=f'{text}.TButton', width=20)
            btn.pack(side=tk.LEFT, padx=10)

        # Config frame
        self.config_frame = ttk.LabelFrame(main_frame, text="Edit Configuration", padding="20")
        self.config_frame.pack(fill=tk.BOTH, expand=True, pady=20)
        self.config_frame.configure(relief="solid", borderwidth=1, style='TLabelframe')

        # Use grid for better alignment and responsiveness
        self.config_frame.columnconfigure(1, weight=1)
        for i, field in enumerate(self.fields):
            label = ttk.Label(self.config_frame, text=f"{field}:", width=12, anchor="w", font=("Helvetica", 10, "bold"))
            label.grid(row=i, column=0, pady=10, padx=5, sticky="w")

            entry = ttk.Entry(self.config_frame, font=("Helvetica", 10), validate="key")
            entry.grid(row=i, column=1, pady=10, padx=5, sticky="ew")
            entry['validatecommand'] = (entry.register(self.validate_entry), '%P', field)
            self.entries[field] = entry

        # Status bar
        self.status = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor="w", style='Status.TLabel')
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    def on_resize(self, event):
        # Can add more logic if needed for responsiveness
        pass

    def darken_color(self, color, factor=0.8):
        color = color.lstrip('#')
        rgb = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
        darkened = tuple(int(c * factor) for c in rgb)
        return f'#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}'

    def validate_entry(self, value, field):
        valid = self.validators.get(field, lambda x: True)(value)
        entry = self.entries[field]
        if valid:
            entry.configure(style='TEntry')
        else:
            entry.configure(style='Error.TEntry')
        return True  # Always allow input, but highlight

    def validate_icc_pno(self, value):
        return bool(re.match(r'^E\d{8}$', value)) or value == ""

    def validate_vin(self, value):
        return len(value) == 17 and value.isalnum() or value == ""

    def validate_hex(self, value):
        return bool(re.match(r'^[0-9A-Fa-f]{32}$', value)) or value == ""

    def validate_hex_short(self, value):
        return bool(re.match(r'^[0-9A-Fa-f]{4}$', value)) or value == ""

    def check_all_valid(self):
        for field in self.fields:
            value = self.entries[field].get()
            if not self.validators.get(field, lambda x: True)(value):
                return False
        return True

    def update_status(self, message, color="#495057"):
        self.status.config(text=message, foreground=color)
        self.root.update()

    def run_adb_command(self, args, capture_output=True):
        try:
            result = subprocess.run(["adb"] + args, capture_output=capture_output, text=True, encoding="utf-8", timeout=30)
            if result.returncode != 0:
                err_msg = result.stderr.strip() or "ADB command failed"
                friendly_msg = self.get_friendly_error(err_msg)
                raise Exception(friendly_msg)
            logging.info(f"ADB command successful: {' '.join(args)}")
            return result.stdout.strip() if capture_output else None
        except subprocess.TimeoutExpired:
            raise Exception("ADB command timed out. Check device connection.")
        except Exception as e:
            logging.error(f"ADB Error: {str(e)}", exc_info=True)
            raise

    def get_friendly_error(self, err_msg):
        if "device not found" in err_msg:
            return "Device not connected. Please check USB connection and ADB settings."
        elif "permission denied" in err_msg:
            return "Permission denied. Ensure root access is granted."
        elif "no space left" in err_msg:
            return "No space left on device. Free up storage."
        else:
            return f"Unexpected error: {err_msg}"

    def get_root_access(self):
        self.update_status("Acquiring root access...")
        self.run_adb_command(["shell", f"setprop service.adb.root.password {self.root_password}"], capture_output=False)
        self.run_adb_command(["root"], capture_output=False)
        time.sleep(3)  # Wait for adbd restart

        # Verify root
        uid = self.run_adb_command(["shell", "id"])
        if "uid=0" not in uid:
            raise Exception("Failed to acquire root access. Check password.")

        # Remount
        self.run_adb_command(["remount"], capture_output=False)

    def backup_remote_file(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(self.backup_dir, f"DeviceInfo_{timestamp}.txt")
        self.run_adb_command(["pull", self.remote_path, backup_file], capture_output=False)
        logging.info(f"Backup created: {backup_file}")
        return backup_file

    def pull_from_device(self):
        def thread_func():
            try:
                self.update_status("Waiting for device...")
                self.run_adb_command(["wait-for-device"])

                self.get_root_access()

                self.update_status("Checking remote file...")
                exists = self.run_adb_command(["shell", f"ls {self.remote_path}"])
                if not exists:
                    raise Exception("Remote file not found")

                self.update_status("Pulling file...")
                self.run_adb_command(["pull", self.remote_path, self.local_file], capture_output=False)

                self.load_local_config()
                self.update_status("Pull successful", "#28a745")
                messagebox.showinfo("Success", "File pulled from device successfully", parent=self.root)
            except Exception as e:
                self.update_status(f"Error: {str(e)}", "#dc3545")
                messagebox.showerror("Error", str(e), parent=self.root)
                logging.error(f"Pull error: {str(e)}")

        Thread(target=thread_func).start()

    def push_to_device(self):
        def thread_func():
            try:
                if not self.check_all_valid():
                    raise Exception("Invalid input in one or more fields. Please correct.")

                if not self.save_locally(silent=True):
                    return

                confirm = messagebox.askyesno("Confirm Root Access", "Proceed with pushing to device? This requires root access.", parent=self.root)
                if not confirm:
                    return

                self.update_status("Waiting for device...")
                self.run_adb_command(["wait-for-device"])

                self.get_root_access()

                self.update_status("Backing up remote file...")
                backup_file = self.backup_remote_file()

                self.update_status("Pushing file...")
                self.run_adb_command(["push", self.local_file, self.remote_path], capture_output=False)

                # Verify
                ls = self.run_adb_command(["shell", f"ls -l {self.remote_path}"])
                self.update_status("Push successful", "#28a745")
                messagebox.showinfo("Success", f"File pushed successfully\nBackup: {backup_file}\n{ls}", parent=self.root)
            except Exception as e:
                self.update_status(f"Error: {str(e)}", "#dc3545")
                messagebox.showerror("Error", str(e), parent=self.root)
                logging.error(f"Push error: {str(e)}")

        Thread(target=thread_func).start()

    def restore_backup(self):
        backups = [f for f in os.listdir(self.backup_dir) if f.startswith("DeviceInfo_") and f.endswith(".txt")]
        if not backups:
            messagebox.showinfo("No Backups", "No backup files found.", parent=self.root)
            return

        backup_file = filedialog.askopenfilename(initialdir=self.backup_dir, filetypes=[("Text files", "*.txt")], parent=self.root)
        if backup_file:
            self.local_file = backup_file
            self.load_local_config()
            messagebox.showinfo("Success", f"Restored from {os.path.basename(backup_file)}", parent=self.root)

    def load_from_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")], parent=self.root)
        if file_path:
            self.local_file = file_path
            self.load_local_config()

    def load_local_config(self):
        if os.path.exists(self.local_file):
            try:
                with open(self.local_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                for field in self.fields:
                    self.entries[field].delete(0, tk.END)
                    self.entries[field].insert(0, self.config.get(field, ""))
                self.validate_entry(self.entries[field].get(), field)  # Validate on load
                self.update_status(f"Loaded: {os.path.basename(self.local_file)}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load config: {str(e)}", parent=self.root)
                logging.error(f"Load error: {str(e)}")
        else:
            self.update_status("No local file found")

    def save_locally(self, silent=False):
        try:
            if not self.check_all_valid():
                raise Exception("Invalid input in one or more fields. Please correct.")

            for field in self.fields:
                self.config[field] = self.entries[field].get()

            with open(self.local_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=None)

            self.update_status(f"Saved: {os.path.basename(self.local_file)}")
            if not silent:
                messagebox.showinfo("Success", "Config saved locally", parent=self.root)
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {str(e)}", parent=self.root)
            logging.error(f"Save error: {str(e)}")
            return False

if __name__ == "__main__":
    root = tk.Tk()
    app = OTAConfigEditor(root)
    root.mainloop()
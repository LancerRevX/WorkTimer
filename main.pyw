import tkinter as tk
from tkinter import ttk
from tkinter.simpledialog import askfloat, askstring
from tkinter.messagebox import askyesno, showinfo
from time import sleep
import sqlite3
from datetime import datetime
from threading import Thread
from dataclasses import dataclass
import os

BUTTON_WIDTH = 10
PADDING = 8
DATETIME_FORMAT_DISPLAY = "%H:%M:%S %d.%m.%y"
TIMER_DELAY = 1000
AUTOSAVE_PERIOD = 5000

DEBUG = os.getenv("DEBUG")


@dataclass
class WorkRecord:
    id: int
    start_datetime: datetime
    end_datetime: datetime
    treeview_item: str | None = None


@dataclass
class Project:
    id: int
    name: str
    rate: float
    active: bool
    work_records: list[WorkRecord]
    treeview_item: str = None


def format_seconds(seconds: int):
    minutes = seconds // 60
    hours = minutes // 60
    minutes = minutes % 60
    seconds = seconds % 60
    result = ""
    if hours > 0:
        result += f"{hours} ч. "
    if minutes > 0:
        result += f"{minutes} м. "
    result += f"{seconds} с."
    return result


def calculate_money(seconds: int, rate: float):
    return seconds / 60 / 60 * rate


def format_money(money: float):
    return f"{money:.2f} руб."


def format_status(active: bool):
    return "Активен" if active else "Завершён"


def get_project_seconds(project: Project):
    total_seconds = 0
    for record in project.work_records:
        total_seconds += int((record.end_datetime - record.start_datetime).total_seconds())
    return total_seconds


class WorkTimerDatabase:
    def __init__(self, database_filename: str):
        self.db_con = sqlite3.connect(database_filename)

        self.db_cur = self.db_con.cursor()
        self.db_cur.execute("CREATE TABLE IF NOT EXISTS config ("
                            "key TEXT NOT NULL UNIQUE,"
                            "value TEXT NOT NULL )")
        self.db_cur.execute("CREATE TABLE IF NOT EXISTS work_record ("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,"
                            "start_datetime TEXT NOT NULL,"
                            "end_datetime TEXT NOT NULL,"
                            "project_id INTEGER REFERENCES work_record (id) ON DELETE CASCADE NOT NULL)")
        self.db_cur.execute("CREATE TABLE IF NOT EXISTS project ("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,"
                            "description TEXT NOT NULL,"
                            "active INTEGER NOT NULL DEFAULT TRUE,"
                            "rate REAL NOT NULL )")

        if not self.db_cur.execute("SELECT key FROM config WHERE key = 'rate'").fetchone():
            self.db_cur.execute("INSERT INTO config (key, value) VALUES ('rate', 500.0)")
            self.db_con.commit()

    def get_projects(self) -> list[Project]:
        def work_record_from_data(data):
            return WorkRecord(data[0], datetime.fromisoformat(data[1]), datetime.fromisoformat(data[2]))
        projects_data = \
            self.db_cur.execute("SELECT id, description, active, rate FROM project").fetchall()
        work_records_data = \
            self.db_cur.execute("SELECT id, start_datetime, end_datetime, project_id FROM work_record").fetchall()
        projects = []
        for project_id, name, active, rate in projects_data:
            project_records = filter(lambda data: data[3] == project_id, work_records_data)
            project_records = list(map(work_record_from_data, project_records))
            projects.append(Project(project_id, name, rate, active, project_records))
        return projects

    def get_default_rate(self) -> float:
        return float(self.db_cur.execute("SELECT value FROM config WHERE key = 'rate'").fetchone()[0])

    def set_default_rate(self, new_rate):
        self.db_cur.execute("UPDATE config SET value = (?) WHERE key = 'rate'", [new_rate])
        self.db_con.commit()

    def add_project(self, name: str, rate: float):
        self.db_cur.execute("INSERT INTO project (description, rate) VALUES (?, ?)", [name, rate])
        self.db_con.commit()
        project_id = \
            self.db_cur.execute("SELECT id FROM project WHERE rowid = ?", [self.db_cur.lastrowid]).fetchone()[0]
        project = Project(project_id, name, rate, True, [])
        return project

    def update_project(self, project: Project, *, name = None, rate = None, active = None):
        if name:
            project.name = name
            self.db_cur.execute("UPDATE project SET description = ? WHERE id = ?", [name, project.id])
        if rate:
            project.rate = rate
            self.db_cur.execute("UPDATE project SET rate = ? WHERE id = ?", [rate, project.id])
        if active:
            project.active = False
            self.db_cur.execute("UPDATE project SET active = FALSE WHERE id = ?", [project.id])
        self.db_con.commit()

    def add_work_record(self, start_datetime: datetime, project: Project) -> WorkRecord:
        self.db_cur.execute("INSERT INTO work_record (start_datetime, end_datetime, project_id) VALUES (?, ?, ?)",
                            [start_datetime, start_datetime, project.id])
        self.db_con.commit()
        record_id = \
            self.db_cur.execute("SELECT id FROM work_record WHERE rowid = ?", [self.db_cur.lastrowid]).fetchone()[0]
        work_record = WorkRecord(record_id, start_datetime, start_datetime)
        project.work_records.append(work_record)
        return work_record

    def update_work_record(self, work_record: WorkRecord, *, end_datetime: datetime):
        work_record.end_datetime = end_datetime
        self.db_cur.execute("UPDATE work_record SET end_datetime = ? WHERE id = ?",
                            [end_datetime.isoformat(), work_record.id])
        self.db_con.commit()

    def __del__(self):
        print("Destructor called...")
        self.db_con.close()


class WorkTimerInterface(tk.Tk):
    def __init__(self):
        super().__init__()

        self.db = WorkTimerDatabase("debug_database.sqlite3" if DEBUG else "database.sqlite3")
        self.default_rate = self.db.get_default_rate()
        self.timer_active = False
        self.current_project: Project | None = None
        self.current_record: WorkRecord | None = None

        self.wm_protocol("WM_DELETE_WINDOW", self.on_close)

        self.title("WorkTimer v.3")
        self.geometry("900x480")

        menubar = tk.Menu(self)
        self.project_menu = tk.Menu(menubar, tearoff=0)
        self.project_menu.add_command(label="Новый", command=self.create_new_project)
        self.project_menu.add_separator()
        self.project_menu.add_command(label="Название",
                                      command=lambda: self.rename_project(self.current_project))
        self.project_menu.add_command(label="Ставка",
                                      command=lambda: self.change_project_rate(self.current_project))
        self.project_menu.add_command(label="Завершить",
                                      command=lambda: self.finish_project(self.current_project))
        menubar.add_cascade(label="Проект", menu=self.project_menu)
        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="Ставка по умолчанию", command=self.set_default_rate)
        menubar.add_cascade(label="Настройки", menu=config_menu)
        self.config(menu=menubar)

        columns = {
            "id": "ID",
            "name": "Название",
            "status": "Статус",
            "rate": "Ставка",
            "start_datetime": "Начало",
            "end_datetime": "Конец",
            "total_time": "Время",
            "money": "Заработано"
        }
        self.projects_table = ttk.Treeview(self,
                                           columns=list(columns.keys()),
                                           selectmode="none",
                                           displaycolumns=list(filter(lambda key: key != "id", columns.keys())))
        self.projects_table.column("#0", width=24, stretch=False)

        for column_id, heading in columns.items():
            self.projects_table.heading(column_id, text=heading)
        self.projects_table.column("id", width=24, stretch=False)
        self.projects_table.column("status", width=80, stretch=False)
        self.projects_table.column("rate", width=80, stretch=False)
        self.projects_table.column("start_datetime", width=130, stretch=False)
        self.projects_table.column("end_datetime", width=130, stretch=False)
        self.projects_table.column("total_time", width=115, stretch=False)
        self.projects_table.column("money", width=120, stretch=False)
        self.projects_table.tag_configure("finished", background="lightgray")
        self.projects_table.pack(fill="both", expand=1, padx=PADDING, pady=PADDING)

        control_frame = tk.Frame(self)
        self.start_button = tk.Button(control_frame, text="Начать", width=BUTTON_WIDTH,
                                      command=self.start_timer)
        self.start_button.pack(side="left")
        self.pause_button = tk.Button(control_frame, text="Остановить", width=BUTTON_WIDTH,
                                      command=self.pause_timer)
        self.pause_button.pack(side="left")
        control_frame.pack(pady=(0, PADDING))

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.projects = self.db.get_projects()
        for project in self.projects:
            self.insert_project_into_treeview(project)
        if self.projects and self.projects[-1].active:
            self.select_project(self.projects[-1])

        self.update_buttons()

    def start_timer(self):
        def update_timer():
            while self.timer_active:
                print("timer")
                now = datetime.now()
                self.current_record.end_datetime = now
                seconds = int((now - self.current_record.start_datetime).total_seconds())
                self.projects_table.set(self.current_record.treeview_item, "end_datetime",
                                        now.strftime(DATETIME_FORMAT_DISPLAY))
                self.projects_table.set(self.current_record.treeview_item, "total_time",
                                        format_seconds(seconds))
                self.projects_table.set(self.current_record.treeview_item, "money",
                                        format_money(calculate_money(seconds, self.current_project.rate)))
                self.update_project(self.current_project)
                sleep(TIMER_DELAY / 1000)

        def autosave():
            if self.timer_active:
                now = datetime.now()
                self.db.update_work_record(self.current_record, end_datetime=now)
                self.after(AUTOSAVE_PERIOD, autosave)

        if self.current_project is None:
            return

        self.timer_active = True

        record = self.db.add_work_record(datetime.now(), self.current_project)
        self.insert_record_into_treeview(record, self.current_project)
        self.current_record = record
        Thread(target=update_timer).start()
        autosave()
        self.update_buttons()

    def pause_timer(self):
        self.timer_active = False
        now = datetime.now()
        self.db.update_work_record(self.current_record, end_datetime=now)
        seconds = int((self.current_record.end_datetime - self.current_record.start_datetime)
                      .total_seconds())
        self.projects_table.set(self.current_record.treeview_item, "end_datetime",
                                self.current_record.end_datetime.strftime(DATETIME_FORMAT_DISPLAY))
        self.projects_table.set(self.current_record.treeview_item, "total_time",
                                format_seconds(seconds))
        self.projects_table.set(self.current_record.treeview_item, "money",
                                format_money(calculate_money(seconds, self.current_project.rate)))
        self.update_project(self.current_project)
        self.update_buttons()

    def create_new_project(self):
        name = askstring("Название", "Введите название для нового проекта:")
        if not name:
            return
        rate = askfloat("Ставка", "Введите ставку для нового проекта:", initialvalue=self.default_rate)
        if not rate:
            return

        project = self.db.add_project(name, rate)
        self.insert_project_into_treeview(project)
        self.projects.append(project)

        self.select_project(project)

    def insert_record_into_treeview(self, record: WorkRecord, project: Project):
        if record.end_datetime is None:
            record.end_datetime = record.start_datetime
        seconds = int((record.end_datetime - record.start_datetime).total_seconds())
        record.treeview_item = \
            self.projects_table.insert(project.treeview_item, 0,
                                       values=[
                                           "", "", "", "",
                                           record.start_datetime.strftime(DATETIME_FORMAT_DISPLAY),
                                           record.end_datetime.strftime(DATETIME_FORMAT_DISPLAY),
                                           format_seconds(seconds),
                                           format_money(calculate_money(seconds, project.rate))
                                       ])

    def insert_project_into_treeview(self, project: Project):
        project.treeview_item = self.projects_table.insert("", 0, values=[
            project.id, project.name, format_status(project.active), format_money(project.rate)])

        total_seconds = 0
        for record_i, record in enumerate(project.work_records):
            start_datetime_formatted = record.start_datetime.strftime(DATETIME_FORMAT_DISPLAY)
            end_datetime_formatted = record.end_datetime.strftime(DATETIME_FORMAT_DISPLAY)
            if record_i == 0:
                self.projects_table.set(project.treeview_item, "start_datetime", start_datetime_formatted)
            if record_i == len(project.work_records) - 1:
                self.projects_table.set(project.treeview_item, "end_datetime", end_datetime_formatted)
            seconds = int((record.end_datetime - record.start_datetime).total_seconds())
            self.projects_table.insert(project.treeview_item, 0, values=[
                "", "", "", "",
                start_datetime_formatted,
                end_datetime_formatted,
                format_seconds(seconds),
                format_money(calculate_money(seconds, project.rate))
            ], tags=["active" if project.active else "finished"])
            total_seconds += seconds
        self.projects_table.set(project.treeview_item,
                                "total_time",
                                format_seconds(total_seconds))
        self.projects_table.set(project.treeview_item,
                                "money",
                                format_money(calculate_money(total_seconds, project.rate)))

        if project.active:
            self.projects_table.item(project.treeview_item,
                                     open=True,
                                     tags=["active", project.treeview_item])
            self.projects_table.tag_bind(project.treeview_item,
                                         "<Button-1>",
                                         lambda event: self.select_project(project))
        else:
            self.projects_table.item(project.treeview_item, tags=["finished"])

    def select_project(self, project: Project):
        if self.timer_active:
            return
        self.projects_table.selection_set(project.treeview_item)
        self.current_project = project
        self.update_buttons()

    def update_buttons(self):
        if self.current_project is not None:
            self.project_menu.entryconfig("Название", state="normal")
            self.project_menu.entryconfig("Ставка", state="normal")
            if len(self.current_project.work_records) > 0:
                self.project_menu.entryconfig("Завершить", state="normal")
            else:
                self.project_menu.entryconfig("Завершить", state="disabled")
            if self.timer_active:
                self.start_button.config(state="disabled")
                self.pause_button.config(state="normal")
            else:
                self.start_button.config(state="normal")
                self.pause_button.config(state="disabled")
        else:
            self.project_menu.entryconfig("Название", state="disabled")
            self.project_menu.entryconfig("Ставка", state="disabled")
            self.project_menu.entryconfig("Завершить", state="disabled")
            self.start_button.config(state="disabled")
            self.pause_button.config(state="disabled")

    def update_project(self, project: Project):
        total_seconds = sum(map(lambda record: int((record.end_datetime - record.start_datetime).total_seconds()),
                                project.work_records))
        print("update project", total_seconds)
        self.projects_table.set(project.treeview_item,
                                "end_datetime",
                                project.work_records[-1].end_datetime.strftime(DATETIME_FORMAT_DISPLAY))
        self.projects_table.set(project.treeview_item,
                                "total_time",
                                format_seconds(total_seconds))
        self.projects_table.set(project.treeview_item,
                                "money",
                                format_money(calculate_money(total_seconds, project.rate)))

    def set_default_rate(self):
        new_rate = askfloat("Ставка по умолчанию",
                        f"Текущая ставка = {format_money(self.default_rate)} Укажи новую ставку в рублях:")
        if new_rate is not None:
            self.default_rate = new_rate
            self.db.set_default_rate(new_rate)

    def rename_project(self, project: Project):
        new_name = askstring("Название проекта", f"Название текущего проекта = "
                                                 f"\"{project.name}\". "
                                                 f"Введи новое название для этого проекта:",
                             initialvalue=project.name)
        if not new_name:
            return
        self.projects_table.set(project.treeview_item, "name", new_name)
        self.db.update_project(project, name=new_name)

    def change_project_rate(self, project):
        new_rate = askfloat("Ставка проекта",
                            f"Текущая ставка проекта = {format_money(project.rate)}. "
                            f"Введи новую ставку для этого проекта:",
                            initialvalue=project.rate)
        if not new_rate:
            return
        self.projects_table.set(project.treeview_item, "rate", format_money(new_rate))
        self.db.update_project(project, rate=new_rate)

    def finish_project(self, project):
        project.active = False
        self.projects_table.set(project.treeview_item, "status", format_status(project.active))
        self.projects_table.item(project.treeview_item, tags=["finished"])
        self.projects_table.selection_remove(project.treeview_item)
        self.db.update_project(project, active=False)
        self.update_buttons()

    def on_close(self):
        print("Destroying window...")
        self.timer_active = False
        self.destroy()


if __name__ == "__main__":
    WorkTimerInterface().mainloop()

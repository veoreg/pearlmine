# SPDX-License-Identifier: Apache-2.0
"""Desktop control center. No automatic mining, download, service or pool connection."""
from dataclasses import asdict
import json
from pathlib import Path
from queue import Empty,Queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog,messagebox,ttk
import webbrowser

from . import pool_stats,theme
from .config import Config
from .control_center import DeviceService,ROOT,atomic_json,human_error
from .fleet import Fleet,SshPeer
from .remote_control import Pairing

now_for_rows=time.time
POOL_EVERY_SECONDS=300
POOL_RETRY_SECONDS=60
BG,PANEL,CARD,TEXT,MUTED=theme.WHITE,theme.SURFACE,theme.SURFACE,theme.TEXT,theme.MUTED
STATES={"viewing":"На связи","busy":"Занята","idle":"Готово","preparing":"Подготовка","running":"Проверка","paused":"Приостановлено","stopping":"Останавливается","stopped":"Остановлено","failed":"Нужна проверка"}


class Dashboard:
    def __init__(self,root,*,service=None,fleet=None,auto_discover=True,settings_path=None):
        self.root=root;self.service=service or DeviceService();self.fleet=fleet or Fleet()
        self.settings_path=Path(settings_path) if settings_path else ROOT/"state/control-center/settings.json"
        self.checked=set();self.remote_checked=set();self.peer_mine=set();self.closing=False;self.background=[];self.events=Queue();self.saved_peers=[]
        self.pages={};self.nav={};self.last_log="";self.current_peer=None;self.agent_windows=[];self.saved_config=Config()
        self.pool_reader=pool_stats.fetch if auto_discover else None;self.pool_stats=self.pool_wallet=self.pool_result=None;self.pool_failed=self.pool_reading=False;self.pool_next=0
        root.title("PearlMine — центр управления");root.geometry("1220x840");root.minsize(1060,760)
        theme.apply(root)
        shell=ttk.Frame(root);shell.pack(fill="both",expand=True)
        side=tk.Frame(shell,bg=theme.GRAPHITE,width=195);side.pack(side="left",fill="y");side.pack_propagate(False)
        tk.Label(side,text="PEARL\nMINE",font=("Segoe UI",21,"bold"),bg=theme.GRAPHITE,fg=theme.ON_GRAPHITE,justify="left").pack(anchor="w",padx=24,pady=(26,6))
        tk.Label(side,text="ЦЕНТР УПРАВЛЕНИЯ",font=("Segoe UI",8,"bold"),bg=theme.GRAPHITE,fg=theme.ON_GRAPHITE_MUTED).pack(anchor="w",padx=24,pady=(0,28))
        for key,title in (("home","Обзор"),("fleet","Компьютеры"),("settings","Кошелёк и пул"),("logs","Журнал"),("help","Помощь")):
            button=tk.Button(side,text=title,command=lambda k=key:self.page(k),font=("Segoe UI",11),anchor="w",bg=theme.GRAPHITE,fg=theme.ON_GRAPHITE,activebackground=theme.GRAPHITE_LIGHT,activeforeground=theme.ACCENT_ON_GRAPHITE,relief="flat",borderwidth=0,padx=24,pady=14,cursor="hand2")
            button.pack(fill="x",padx=8,pady=2);self.nav[key]=button
        tk.Label(side,text="ПРЕДВАРИТЕЛЬНЫЙ ВЫПУСК\n\nЛокальные тесты: 0%\nНикаких скрытых загрузок",font=("Segoe UI",9),bg=theme.GRAPHITE,fg=theme.ON_GRAPHITE_MUTED,justify="left",wraplength=160).pack(side="bottom",anchor="w",padx=20,pady=24)
        main=ttk.Frame(shell,padding=(26,22));main.pack(side="left",fill="both",expand=True)
        head=ttk.Frame(main);head.pack(fill="x")
        ttk.Label(head,text="Ваши устройства",style="Title.TLabel").pack(side="left")
        ttk.Button(head,text="Остановить всё",style="Stop.TButton",command=self.stop_all).pack(side="right")
        ttk.Label(main,text="Этот компьютер и ваши подключённые устройства — в одной панели.",style="Muted.TLabel").pack(anchor="w",pady=(4,15))
        self.banner=tk.StringVar(value="Предварительная версия · майнинг работает на RTX 5090. Остальные карты ещё в работе.")
        tk.Label(main,textvariable=self.banner,bg=theme.SOFT,fg=TEXT,padx=14,pady=10,anchor="w",wraplength=950,justify="left").pack(fill="x",pady=(0,16))
        self.footer=tk.StringVar(value="Ожидание · запуск вычислений только по вашей кнопке")
        ttk.Label(main,textvariable=self.footer,style="Muted.TLabel",wraplength=950).pack(side="bottom",anchor="w",pady=(12,0))
        self.content=ttk.Frame(main);self.content.pack(fill="both",expand=True)
        self._home();self._fleet();self._settings();self._logs();self._help()
        root.boris_dashboard=self;root.protocol("WM_DELETE_WINDOW",self.close)
        self._load();self.page("home");self.poll()
        if settings_path is None:
            from .shortcut import create_once
            if create_once(self.settings_path.parent):self.footer.set("Ярлык PearlMine добавлен на рабочий стол и в меню «Пуск».")
        if auto_discover:root.after(250,self.refresh)

    def page(self,key):
        for name,frame in self.pages.items():
            if name==key:frame.pack(fill="both",expand=True)
            else:frame.pack_forget()
            self.nav[name].configure(bg=theme.GRAPHITE_LIGHT if name==key else theme.GRAPHITE,fg=theme.ACCENT_ON_GRAPHITE if name==key else theme.ON_GRAPHITE)

    def frame(self,key):
        frame=ttk.Frame(self.content);self.pages[key]=frame;return frame

    def _home(self):
        home=self.frame("home");metrics=ttk.Frame(home);metrics.pack(fill="x",pady=(0,18));self.metrics={}
        for key,label,initial in (("devices","ВСЕГО УСТРОЙСТВ","—"),("active","В РАБОТЕ","0"),("power","ПОТРЕБЛЕНИЕ","—"),("credited","ЗАЧИСЛЕНО НА ПУЛЕ","—")):
            card=tk.Frame(metrics,bg=CARD,padx=18,pady=14,highlightthickness=1,highlightbackground=theme.LINE);card.pack(side="left",fill="x",expand=True,padx=(0,10))
            tk.Label(card,text=label,bg=CARD,fg=MUTED,font=("Segoe UI",9,"bold")).pack(anchor="w")
            var=tk.StringVar(value=initial);tk.Label(card,textvariable=var,bg=CARD,fg=TEXT,font=("Segoe UI",24,"bold")).pack(anchor="w",pady=(5,0));self.metrics[key]=var
        bar=ttk.Frame(home);bar.pack(fill="x",pady=(0,10))
        ttk.Label(bar,text="Все карты",font=("Segoe UI",15,"bold")).pack(side="left")
        self.refresh_button=ttk.Button(bar,text="Обновить устройства",command=self.refresh);self.refresh_button.pack(side="right")
        ttk.Button(bar,text="Выбрать доступные",command=self.select_all).pack(side="right",padx=8)
        self.table=self._table(home,[("check","",38),("name","Устройство",230),("state","Состояние",140),("temp","°C",60),("watts","Вт",70),("memory","Свободно",95),("note","Готовность",240)],height=3)
        self.table.bind("<Button-1>",self.toggle);self.table.bind("<space>",lambda e:self.toggle_focus())
        self.empty=tk.StringVar(value="Проверяем устройства…")
        ttk.Label(home,textvariable=self.empty,style="Muted.TLabel",wraplength=930).pack(anchor="w",pady=(8,15))
        controls=ttk.Frame(home);controls.pack(fill="x",pady=4)
        ttk.Label(controls,text="Режим").pack(side="left");self.profile=tk.StringVar(value="Обычный компьютер")
        ttk.Combobox(controls,textvariable=self.profile,values=("Обычный компьютер","Ноутбук"),state="readonly",width=20).pack(side="left",padx=(8,18))
        ttk.Label(controls,text="Длительность").pack(side="left");self.duration=tk.StringVar(value="1 мин")
        ttk.Combobox(controls,textvariable=self.duration,values=("30 сек","1 мин","3 мин","5 мин"),state="readonly",width=9).pack(side="left",padx=8)
        self.start_button=ttk.Button(controls,text="Тест без пула",command=self.start);self.start_button.pack(side="right")
        mining=ttk.Frame(home);mining.pack(fill="x",pady=(8,12))
        ttk.Label(mining,text="Нагрузка").pack(side="left");self.load=tk.StringVar(value="100%")
        ttk.Combobox(mining,textvariable=self.load,values=("25%","50%","100%"),state="readonly",width=8).pack(side="left",padx=(8,18))
        ttk.Label(mining,text="Майнить").pack(side="left");self.mine_for=tk.StringVar(value="24 часа")
        ttk.Combobox(mining,textvariable=self.mine_for,values=("10 мин","20 мин","30 мин","1 час","8 часов","24 часа","7 дней"),state="readonly",width=9).pack(side="left",padx=8)
        self.mine_button=ttk.Button(mining,text="Начать майнинг",style="Primary.TButton",command=self.start_mining);self.mine_button.pack(side="right")
        detail=tk.Frame(home,bg=CARD,padx=18,pady=14,highlightthickness=1,highlightbackground=theme.LINE);detail.pack(fill="x")
        title=tk.Frame(detail,bg=CARD);title.pack(fill="x")
        tk.Label(title,text="Что происходит сейчас",bg=CARD,fg=TEXT,font=("Segoe UI",12,"bold")).pack(side="left")
        self.pool_button=ttk.Button(title,text="Открыть на сайте пула",command=self.open_pool)
        self.activity=tk.StringVar(value="Вычисления не запущены. Ничего не загружается.")
        tk.Label(detail,textvariable=self.activity,bg=CARD,fg=MUTED,font=("Segoe UI",10),wraplength=880,justify="left").pack(anchor="w",pady=(7,0))
        self.pool_line=tk.StringVar();tk.Label(detail,textvariable=self.pool_line,bg=CARD,fg=MUTED,font=("Segoe UI",10),wraplength=880,justify="left").pack(anchor="w",pady=(7,0))

    def open_pool(self):
        webbrowser.open(pool_stats.page_url(self.saved_config.user_wallet))

    def _pool_read(self,wallet):
        try:self.pool_result=(wallet,self.pool_reader(wallet),None)
        except Exception as exc:self.pool_result=(wallet,None,exc)

    def _pool_tick(self,now):
        """The saved address only, every few minutes, off the UI thread; an old figure stays up if the pool is unreachable."""
        config=self.saved_config;wallet=config.user_wallet
        if not self.pool_reader or not pool_stats.supported(wallet,config.user_pool):
            self.pool_wallet=self.pool_stats=None;self.metrics["credited"].set("—");self.pool_line.set("");self.pool_button.pack_forget();return
        if wallet!=self.pool_wallet:
            self.pool_wallet=wallet;self.pool_stats=None;self.pool_failed=False;self.pool_next=0;self.pool_button.pack(side="right")
        result,self.pool_result=self.pool_result,None
        if result:
            self.pool_reading=False
            if result[0]==wallet:
                self.pool_failed=result[2] is not None
                if result[1]:self.pool_stats=result[1]
                if self.pool_failed:self.pool_next=now+POOL_RETRY_SECONDS
        if now>=self.pool_next and not self.pool_reading:
            self.pool_reading=True;self.pool_next=now+POOL_EVERY_SECONDS
            threading.Thread(target=self._pool_read,args=(wallet,),name="pearlmine-pool-stats",daemon=True).start()
        stats=self.pool_stats
        self.metrics["credited"].set(pool_stats.credited(stats) if stats else "—")
        self.pool_line.set((pool_stats.summary(stats) if stats else "")+(" Не удалось обновить, показаны прошлые данные." if stats and self.pool_failed else "Пул сейчас не отвечает, повторим позже." if self.pool_failed else "" if stats else "Запрашиваю баланс у пула…"))

    @staticmethod
    def state_text(row):
        if row.get("state")=="idle" and not row.get("eligible"):return "Недоступно"
        if row.get("state")=="running" and str(row.get("note","")).startswith("Майнинг"):return "Майнинг"
        return STATES.get(row.get("state"),row.get("state"))

    def _table(self,parent,columns,height):
        frame=ttk.Frame(parent);frame.pack(fill="both",expand=True)
        view=ttk.Treeview(frame,columns=[c[0] for c in columns],show="headings",height=height,selectmode="extended")
        for key,title,width in columns:view.heading(key,text=title);view.column(key,width=width,minwidth=width if key=="check" else 45,stretch=key in ("name","note"))
        scroll=ttk.Scrollbar(frame,orient="vertical",command=view.yview);view.configure(yscrollcommand=scroll.set)
        view.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y");return view

    def _fleet(self):
        frame=self.frame("fleet")
        ttk.Label(frame,text="Ваши компьютеры",font=("Segoe UI",17,"bold")).pack(anchor="w")
        ttk.Label(frame,text="Можно подключать устройства из разных сетей через частную VPN-сеть. Доступ — только по вашему коду подключения.",style="Muted.TLabel",wraplength=930).pack(anchor="w",pady=(7,12))
        row=ttk.Frame(frame);row.pack(fill="x",pady=(0,12))
        ttk.Button(row,text="Добавить компьютер",style="Primary.TButton",command=self.pair_dialog).pack(side="left")
        ttk.Button(row,text="Разрешить управление этим ПК",command=self.agent_window).pack(side="left",padx=8)
        ttk.Button(row,text="Посмотреть по SSH",command=self.ssh_dialog).pack(side="left")
        ttk.Button(row,text="Убрать выбранный",command=self.remove_peer).pack(side="right")
        self.peer_table=self._table(frame,[("name","Компьютер",220),("state","Подключение",200),("active","В работе",90),("note","Состояние",400)],height=3)
        self.peer_table.bind("<<TreeviewSelect>>",lambda e:self.peer_selected())
        ttk.Label(frame,text="Карты выбранного компьютера",font=("Segoe UI",13,"bold")).pack(anchor="w",pady=(15,8))
        self.remote_table=self._table(frame,[("check","",38),("name","Карта",220),("state","Состояние",145),("temp","°C",60),("watts","Вт",65),("note","Готовность",280)],height=3)
        self.remote_table.bind("<Button-1>",self.toggle_remote)
        row=ttk.Frame(frame);row.pack(fill="x",pady=(12,6))
        ttk.Button(row,text="Выбрать доступные карты",command=self.select_remote).pack(side="left")
        ttk.Button(row,text="Остановить на компьютере",style="Stop.TButton",command=self.stop_remote).pack(side="right")
        ttk.Button(row,text="Запустить тест",style="Primary.TButton",command=self.start_remote).pack(side="right",padx=8)
        ttk.Label(frame,text="При потере связи удалённый тест автоматически остановится. Запуск — только если владелец включил его на агенте.",style="Muted.TLabel",wraplength=930).pack(anchor="w")

    def _settings(self):
        frame=self.frame("settings");ttk.Label(frame,text="Кошелёк и пул",font=("Segoe UI",17,"bold")).pack(anchor="w")
        ttk.Label(frame,text="Достаточно публичного адреса PRL. Секретные ключи и seed-фразы приложению не нужны.",style="Muted.TLabel",wraplength=900).pack(anchor="w",pady=(7,18))
        self.config_vars={}
        for key,title in (("user_wallet","Куда платить: публичный адрес PRL или логин Kryptex"),("user_pool","Ваш пул — stratum+ssl://адрес:порт"),("pool_fee_percent","Комиссия пула, % — оставьте пустым, если неизвестна")):
            ttk.Label(frame,text=title).pack(anchor="w",pady=(5,3));var=tk.StringVar();self.config_vars[key]=var
            if key=="user_pool":ttk.Combobox(frame,textvariable=var,values=tuple(f"stratum+ssl://prl{r}.kryptex.network:8048" for r in ("-sg","","-eu","-us"))).pack(fill="x")
            else:ttk.Entry(frame,textvariable=var).pack(fill="x")
        ttk.Label(frame,text="Комиссия разработчика",font=("Segoe UI",13,"bold")).pack(anchor="w",pady=(14,5))
        self.fee=tk.StringVar(value="0% — свои тесты");ttk.Combobox(frame,textvariable=self.fee,values=("0% — свои тесты","1.5%","2%"),state="readonly",width=22).pack(anchor="w")
        ttk.Label(frame,text="Комиссия считается по активному времени GPU. Комиссия пула указывается отдельно; доля дохода может отличаться.",style="Muted.TLabel",wraplength=930).pack(anchor="w",pady=(7,12))
        self.developer_fields=ttk.Frame(frame);self.developer_fields.columnconfigure((0,1),weight=1,uniform="developer")
        for column,(key,title) in enumerate((("developer_wallet","Публичный адрес комиссии — настройка разработчика"),("developer_pool","Пул для комиссии — настройка разработчика"))):
            ttk.Label(self.developer_fields,text=title).grid(row=0,column=column,sticky="w",padx=(0,10),pady=(0,4));var=tk.StringVar();self.config_vars[key]=var;ttk.Entry(self.developer_fields,textvariable=var).grid(row=1,column=column,sticky="ew",padx=(0,10))
        row=ttk.Frame(frame);row.pack(fill="x",pady=(4,14));self.settings_buttons=row
        self.fee.trace_add("write",lambda *_:self.show_developer_fields())
        ttk.Button(row,text="Сохранить настройки",style="Primary.TButton",command=self.save_settings).pack(side="left")
        ttk.Button(row,text="Проверить",command=self.check_settings).pack(side="left",padx=8)
        self.settings_message=tk.StringVar(value="Сохранение настроек ничего не запускает и не подключает пул.")
        ttk.Label(frame,textvariable=self.settings_message,style="Muted.TLabel",wraplength=930).pack(anchor="w")

    def _logs(self):
        frame=self.frame("logs");row=ttk.Frame(frame);row.pack(fill="x",pady=(0,12))
        ttk.Label(row,text="Журнал и диагностика",font=("Segoe UI",17,"bold")).pack(side="left")
        ttk.Button(row,text="Сохранить отчёт",command=self.export).pack(side="right")
        ttk.Button(row,text="Проверить вычислительный модуль",command=self.worker_check).pack(side="right",padx=8)
        self.log_box=tk.Text(frame,bg=PANEL,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Consolas",10),wrap="word",padx=14,pady=14,state="disabled")
        self.log_box.pack(fill="both",expand=True)

    def _help(self):
        frame=self.frame("help");ttk.Label(frame,text="Простой запуск",font=("Segoe UI",17,"bold")).pack(anchor="w")
        text="1. Откройте PearlMine.exe.\n\n2. На обзоре видны обнаруженные карты и их готовность. Отметьте нужные и запустите тест.\n\n3. В разделе «Кошелёк и пул» один раз сохраните адрес или логин Kryptex и пул. Затем отметьте карту и нажмите «Начать майнинг».\n\n4. Для Spark и других компьютеров откройте «Компьютеры». На удалённом устройстве включите агент и перенесите код подключения в свою панель. Устройства могут находиться в разных местах, если связаны частной VPN-сетью.\n\n«Остановить всё» останавливает локальные и подключённые удалённые тесты. При закрытии программы её процессы завершаются.\n\nНичего не устанавливается и не скачивается автоматически. Поддержка модели показывается отдельно от наличия карты. Windows RTX 5090 прошла локальные проверки; другие модели ещё требуют физических испытаний."
        ttk.Label(frame,text=text,wraplength=880,justify="left",font=("Segoe UI",11)).pack(anchor="w",pady=18)

    def show_developer_fields(self):
        if self.fee.get().startswith("0%"):self.developer_fields.pack_forget()
        else:self.developer_fields.pack(fill="x",pady=(0,8),before=self.settings_buttons)

    def configured(self):
        data={k:v.get().strip() for k,v in self.config_vars.items()};raw=data.pop("pool_fee_percent")
        data["pool_fee_percent"]=None if raw=="" else float(raw.replace(",","."));data["fee_bps"]={"0% — свои тесты":0,"1.5%":150,"2%":200}[self.fee.get()]
        return Config(**data)

    def _save(self):
        # Only the last validated form is written: a half-typed wallet must not block remembering a computer.
        atomic_json(self.settings_path,dict(schema=1,config=asdict(self.saved_config),profile=self.profile.get(),duration=self.duration.get(),paired=self.saved_peers))

    def save_settings(self):
        try:self.saved_config=self.configured();self._save();self.settings_message.set("Настройки сохранены. При следующем запуске они откроются автоматически.");self.service.log("Публичные настройки сохранены; вычисления не запускались.")
        except Exception as exc:self.settings_message.set("Проверьте настройки: "+human_error(exc))

    def check_settings(self):
        try:
            config=self.configured();self.settings_message.set(f"Проверка пройдена. Комиссия разработчика: {config.effective_bps/100:g}%. Настройки не запускают майнинг.")
        except Exception as exc:self.settings_message.set("Проверьте настройки: "+human_error(exc))

    def _load(self):
        if not self.settings_path.is_file():return
        try:
            raw=self.settings_path.read_bytes()
            if len(raw)>131072:raise ValueError("Слишком большой файл настроек.")
            data=json.loads(raw);config=self.saved_config=Config(**data["config"])
            for key,var in self.config_vars.items():var.set("" if getattr(config,key) is None else str(getattr(config,key)))
            self.fee.set({0:"0% — свои тесты",150:"1.5%",200:"2%"}[config.fee_bps])
            if data.get("profile") in ("Обычный компьютер","Ноутбук"):self.profile.set(data["profile"])
            if data.get("duration") in ("30 сек","1 мин","3 мин","5 мин"):self.duration.set(data["duration"])
            from .pairing_store import unseal
            self.saved_peers=data.get("paired",[])[:16]
            for record in self.saved_peers:
                try:
                    if record.get("kind")=="ssh":self.fleet.connect_ssh(SshPeer(record["name"],record["target"],record.get("identity","")))
                    else:self.fleet.connect(unseal(record))
                except Exception:self.service.log("Для одного сохранённого компьютера нужен новый код подключения.")
        except Exception as exc:
            self.service.log("Настройки не загружены: "+human_error(exc)+" Файл сохранён рядом с расширением .bad.")
            try:self.settings_path.replace(self.settings_path.with_name(self.settings_path.name+".bad"))
            except OSError:pass

    def options(self):
        laptop=self.profile.get()=="Ноутбук"
        return dict(seconds={"30 сек":30,"1 мин":60,"3 мин":180,"5 мин":300}[self.duration.get()],duty=.1 if laptop else .2,profile="laptop" if laptop else "standard",backend="auto")

    def start(self):
        try:self.service.start(list(self.checked),**self.options())
        except Exception as exc:self.footer.set(human_error(exc))

    def start_mining(self):
        config=self.saved_config
        if not config.user_wallet or not config.user_pool:
            self.footer.set("Сначала сохраните кошелёк или логин и пул.");self.page("settings");return
        laptop=self.profile.get()=="Ноутбук"
        duty=.1 if laptop else {"25%":.25,"50%":.5,"100%":1}[self.load.get()]
        seconds={"10 мин":600,"20 мин":1200,"30 мин":1800,"1 час":3600,"8 часов":28800,"24 часа":86400,"7 дней":604800}[self.mine_for.get()]
        text=(f"Выплаты: {config.user_wallet}\nПул: {config.user_pool}\nКомиссия пула указана на его сайте, она отдельная.\n"
            f"Комиссия разработчика: {config.effective_bps/100:g}% активного времени GPU.\nНагрузка: {duty*100:g}%, срок: {self.mine_for.get()}.\n\n"
            "Карта будет под высокой нагрузкой и расходует электричество. При нагреве программа сбавит темп, при 83 °C остановится.\n"
            +("На удалённом компьютере майнинг работает отдельно от панели, сам уступает заказам и останавливается кнопкой «Остановить всё».\n" if self.peer_mine else "")+"\nНачать майнинг?")
        if not messagebox.askokcancel("Начать майнинг",text,parent=self.root):return
        try:
            if self.checked:self.service.start_mining(list(self.checked),config,seconds=seconds,duty=duty,profile="laptop" if laptop else "standard")
            for peer in self.peer_mine:self.fleet.miner(peer,"start",config.user_wallet,config.user_pool,seconds)
        except Exception as exc:self.footer.set(human_error(exc))

    def stop_all(self):self.service.stop();self.fleet.stop();self.footer.set("Останавливаю все принадлежащие программе тесты…")
    def stop_remote(self):
        if self.current_peer:self.fleet.stop(self.current_peer);self.footer.set("Останавливаю тесты на выбранном компьютере…")
        else:self.footer.set("Выберите компьютер в таблице.")
    def remove_peer(self):
        key=self.current_peer
        if not key:self.footer.set("Выберите компьютер в таблице.");return
        self.fleet.remove(key);self.saved_peers=[r for r in self.saved_peers if r.get("fingerprint")!=key]
        self.current_peer=None;self.remote_checked=set()
        if self.peer_table.exists(key):self.peer_table.delete(key)
        try:self._save();self.footer.set("Компьютер убран из панели; его тесты останавливаются. Агент на нём закрывается отдельно.")
        except Exception as exc:self.footer.set(human_error(exc))
    def refresh(self):
        try:self.service.refresh()
        except Exception as exc:self.footer.set(human_error(exc))
    def select_all(self):self.checked={key for key,value in self.service.snapshot()["devices"].items() if value["eligible"]}
    def toggle_focus(self):
        key=self.table.focus()
        if key:self._toggle(key)
    def _toggle(self,key):
        if key.startswith("remote:"):
            peer=key.split(":",1)[1].rsplit(":GPU-",1)[0]
            if (self.fleet.snapshot().get(peer,{}).get("status") or {}).get("miner_ready"):self.peer_mine.symmetric_difference_update({peer})
            return
        if self.service.snapshot()["devices"].get(key,{}).get("eligible"):
            if key in self.checked:self.checked.remove(key)
            else:self.checked.add(key)
    def toggle(self,event):
        key=self.table.identify_row(event.y)
        if key and self.table.identify_region(event.x,event.y)=="cell":self._toggle(key);return "break"

    def peer_selected(self):
        selected=self.peer_table.selection();self.current_peer=selected[0] if selected else None;self.remote_checked=set()
    def toggle_remote(self,event):
        key=self.remote_table.identify_row(event.y)
        if key and self.remote_table.identify_region(event.x,event.y)=="cell":
            peer=self.fleet.snapshot().get(self.current_peer,{})
            if key in self.remote_checked:self.remote_checked.remove(key)
            elif (peer.get("status") or {}).get("devices",{}).get(key,{}).get("eligible"):self.remote_checked.add(key)
            return "break"
    def select_remote(self):
        peer=self.fleet.snapshot().get(self.current_peer,{})
        self.remote_checked={k for k,v in (peer.get("status") or {}).get("devices",{}).items() if v.get("eligible")}
    def start_remote(self):
        try:
            if not self.current_peer:raise ValueError("Выберите подключённый компьютер.")
            self.fleet.start(self.current_peer,self.remote_checked,**self.options())
        except Exception as exc:self.footer.set(human_error(exc))

    def ssh_dialog(self):
        window=tk.Toplevel(self.root);window.title("Посмотреть компьютер по SSH");window.geometry("560x330");window.configure(bg=BG)
        ttk.Label(window,text="Только просмотр: температура, потребление, загрузка",font=("Segoe UI",12,"bold")).pack(anchor="w",padx=18,pady=(16,4))
        ttk.Label(window,text="На том компьютере ничего не устанавливается и не запускается. Нужен уже настроенный вход по SSH-ключу.",style="Muted.TLabel",wraplength=520).pack(anchor="w",padx=18)
        fields={}
        for key,title in (("name","Имя в панели"),("target","Адрес: имя из настроек SSH или пользователь@адрес"),("identity","Файл ключа (если нужен особый)")):
            ttk.Label(window,text=title).pack(anchor="w",padx=18,pady=(8,2));fields[key]=tk.StringVar();ttk.Entry(window,textvariable=fields[key]).pack(fill="x",padx=18)
        status=tk.StringVar();ttk.Label(window,textvariable=status,wraplength=520).pack(anchor="w",padx=18,pady=4)
        def connect():
            try:
                peer=SshPeer(fields["name"].get().strip(),fields["target"].get().strip(),fields["identity"].get().strip())
                self.fleet.connect_ssh(peer)
                self.saved_peers.append(dict(kind="ssh",fingerprint=peer.key,name=peer.name,target=peer.target,identity=peer.identity));self._save()
                window.destroy();self.footer.set("Подключаю "+peer.name+"…")
            except Exception as exc:status.set(human_error(exc))
        ttk.Button(window,text="Подключить",style="Primary.TButton",command=connect).pack(anchor="e",padx=18,pady=8)

    def pair_dialog(self):
        window=tk.Toplevel(self.root);window.title("Добавить свой компьютер");window.geometry("640x390");window.configure(bg=BG)
        ttk.Label(window,text="Вставьте код подключения с вашего устройства",font=("Segoe UI",13,"bold")).pack(anchor="w",padx=18,pady=16)
        ttk.Label(window,text="Этот код даёт доступ к управлению. Передавайте его только своей панели.",style="Muted.TLabel",wraplength=600).pack(anchor="w",padx=18)
        box=tk.Text(window,height=8,bg=PANEL,fg=TEXT,insertbackground=TEXT,wrap="word");box.pack(fill="both",expand=True,padx=18,pady=12)
        remember=tk.BooleanVar(value=sys.platform=="win32")
        tk.Checkbutton(window,text="Запомнить на этом компьютере",variable=remember,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT).pack(anchor="w",padx=18)
        status=tk.StringVar();ttk.Label(window,textvariable=status,wraplength=600).pack(anchor="w",padx=18)
        def connect():
            try:
                pairing=Pairing.parse(box.get("1.0","end").strip());record=None
                if remember.get():
                    from .pairing_store import seal
                    record=seal(pairing)
                self.fleet.connect(pairing)
                if record:self.saved_peers.append(record);self._save()
                window.destroy();self.footer.set("Подключаю "+pairing.name+". Проверяю сертификат и доступ…")
            except Exception as exc:status.set(human_error(exc))
        def load():
            path=filedialog.askopenfilename(filetypes=[("Код подключения","*.json")])
            if path:
                try:
                    data=Path(path).read_text(encoding="utf-8")
                    if len(data)>4096:raise ValueError("Слишком большой файл.")
                    box.delete("1.0","end");box.insert("1.0",data)
                except Exception as exc:status.set(human_error(exc))
        row=ttk.Frame(window);row.pack(fill="x",padx=18,pady=14)
        ttk.Button(row,text="Загрузить из файла",command=load).pack(side="left")
        ttk.Button(row,text="Подключить",style="Primary.TButton",command=connect).pack(side="right")

    def agent_window(self):
        from .agent_ui import AgentWindow
        self.agent_windows.append(AgentWindow(tk.Toplevel(self.root)))

    def worker_check(self):
        def work():
            from .ipc import transport_selftest
            try:self.events.put(("log",json.dumps(transport_selftest(),ensure_ascii=False)))
            except Exception as exc:self.events.put(("log",human_error(exc)))
        thread=threading.Thread(target=work,name="boris-ui-synthetic-check",daemon=False);self.background.append(thread);thread.start()

    def export(self):
        path=filedialog.asksaveasfilename(defaultextension=".json",initialfile="boris-diagnostics.json",filetypes=[("Отчёт JSON","*.json")])
        if path:
            try:atomic_json(path,self.service.export());self.footer.set("Отчёт сохранён. Коды подключения и настройки кошелька в него не включены.")
            except Exception as exc:self.footer.set(human_error(exc))

    @staticmethod
    def value(number,suffix="",digits=1):return "—" if number is None else f"{number:.{digits}f}{suffix}"
    def poll(self):
        while True:
            try:kind,value=self.events.get_nowait();self.service.log(value)
            except Empty:break
        snap=self.service.snapshot();devices=snap["devices"]
        self.checked.intersection_update(devices)
        remote_rows={}
        for peer_key,peer in self.fleet.snapshot().items():
            cards=(peer["status"] or {}).get("devices",{});alive=peer["updated_at"] and now_for_rows()-peer["updated_at"]<=15 and not peer["error"]
            if not cards:remote_rows[f"remote:{peer_key}"]=("",peer["name"],"Нет связи" if peer["error"] else "Подключаем…","—","—","—",peer["error"] or "")
            for card_key,card in cards.items():
                free=card.get("free_bytes")
                remote_rows[f"remote:{peer_key}:{card_key}"]=(("☑" if peer_key in self.peer_mine else "☐") if (peer["status"] or {}).get("miner_ready") else "",peer["name"]+" · "+card["name"],self.state_text(card) if alive else "Нет связи",self.value(card.get("temperature") if alive else None,digits=0),self.value(card.get("watts") if alive else None,digits=0),self.value(None if not alive or free is None else free/(1<<30)," ГБ"),card.get("note","") if alive else (peer["error"] or ""))
        for key in self.table.get_children():
            if key not in devices and key not in remote_rows:self.table.delete(key)
        for key,values in remote_rows.items():
            if self.table.exists(key):self.table.item(key,values=values)
            else:self.table.insert("","end",iid=key,values=values)
        now=time.time()
        for key,row in devices.items():
            fresh=row.get("updated_at") is not None and now-row["updated_at"]<=5
            values=("☑" if key in self.checked else "☐",row["name"],self.state_text(row),self.value(row["temperature"] if fresh else None,digits=0),self.value(row["watts"] if fresh else None,digits=0),self.value(None if not fresh or row["free_bytes"] is None else row["free_bytes"]/(1<<30)," ГБ"),row.get("note",""))
            if self.table.exists(key):self.table.item(key,values=values)
            else:self.table.insert("","end",iid=key,values=values)
        powers=[r["watts"] for r in devices.values() if r["watts"] is not None and r.get("updated_at") and now-r["updated_at"]<=5]
        peers=self.fleet.snapshot();online={k:v["status"] for k,v in peers.items() if v["status"] and v["updated_at"] and now-v["updated_at"]<=6 and not v["error"]}
        powers+=[r["watts"] for status in online.values() for r in status.get("devices",{}).values() if r.get("watts") is not None]
        remote_active=sum(status.get("active",0) for status in online.values())
        self.metrics["devices"].set(str(len(devices)+sum(len(status.get("devices",{})) for status in online.values())));self.metrics["active"].set(str(snap["active"]+remote_active))
        self.metrics["power"].set(f"{sum(powers):.0f} Вт" if powers else "—")
        self._pool_tick(now)
        self.refresh_button.state(["disabled"] if snap["refreshing"] or snap["active"] else ["!disabled"])
        self.start_button.state(["!disabled"] if self.checked and not snap["refreshing"] and not self.closing else ["disabled"])
        self.mine_button.state(["!disabled"] if (self.checked or self.peer_mine) and not snap["refreshing"] and not self.closing else ["disabled"])
        self.empty.set("Диагностика устройств…" if snap["refreshing"] else (snap["error"] or ("Нажмите на строку карты, чтобы отметить её." if devices else "Нет проверенного вычислительного модуля или доступных CUDA GPU. Откройте журнал для подробностей.")))
        remote_mining=sum(1 for status in online.values() if (status.get("miner") or {}).get("state")=="running");remote_tests=remote_active-remote_mining
        self.activity.set(f"Идёт майнинг: здесь {snap.get('mining') or 0}, на других компьютерах {remote_mining}. Подключение к вашему пулу открыто. Кнопка «Остановить всё» завершает его сразу." if snap.get("mining") or remote_mining else f"Тесты: здесь {snap['active']}, на других компьютерах {remote_tests}. Без пула и выплат." if snap["active"] or remote_tests else "Вычисления не запущены. Майнинг начинается только по вашей кнопке.")
        logs="\n".join(row["time"]+"  "+row["message"] for row in snap["logs"])
        if logs!=self.last_log:
            self.log_box.configure(state="normal");self.log_box.delete("1.0","end");self.log_box.insert("1.0",logs);self.log_box.see("end");self.log_box.configure(state="disabled");self.last_log=logs
        for key,row in peers.items():
            status=row["status"] or {};fresh=row["updated_at"] and now-row["updated_at"]<=6 and not row["error"]
            values=(row["name"],"На связи" if fresh else "Нет связи" if row["error"] else "Подключаем…",status.get("active","—"),row["error"] or row["notice"] or ("Тесты разрешены владельцем" if status.get("allow_tests") else "Только просмотр"))
            if self.peer_table.exists(key):self.peer_table.item(key,values=values)
            else:self.peer_table.insert("","end",iid=key,values=values)
        remote=((peers.get(self.current_peer) or {}).get("status") or {}).get("devices",{});remote_fresh=self.current_peer in online
        for key in self.remote_table.get_children():
            if key not in remote:self.remote_table.delete(key)
        for key,row in remote.items():
            values=("☑" if key in self.remote_checked else "☐",row["name"],self.state_text(row),self.value(row.get("temperature") if remote_fresh else None,digits=0),self.value(row.get("watts") if remote_fresh else None,digits=0),row.get("note",""))
            if self.remote_table.exists(key):self.remote_table.item(key,values=values)
            else:self.remote_table.insert("","end",iid=key,values=values)
        if self.closing and not self.service.alive() and not self.fleet.alive() and not any(t.is_alive() for t in self.background) and not any(a.alive() for a in self.agent_windows):self.root.destroy();return
        self.root.after(200,self.poll)

    def close(self):
        if self.closing:return
        self.closing=True;self.banner.set("Завершаю процессы и отключаю удалённые тесты. Подождите…")
        try:self._save()
        except Exception:pass
        self.service.close();self.fleet.close()
        for agent in self.agent_windows:
            if agent.window.winfo_exists():agent.close()


def run():
    root=tk.Tk();Dashboard(root);root.mainloop()

import paramiko
import time
import os
import subprocess
from os.path import expanduser
import configparser
import json
import logging
import logging.handlers
import inspect
import signal
from threading import Thread
from telegram.ext import Updater


TERMINATED = False
DEBUG = True


def whoami():
    outer_func_name = str(inspect.getouterframes(inspect.currentframe())[1].function)
    outer_func_linenr = str(inspect.currentframe().f_back.f_lineno)
    return outer_func_name + " / #" + outer_func_linenr + ": "


class SigHandler:
    def __init__(self, logger):
        self.logger = logger

    def sighandler(self, a, b):
        global TERMINATED
        self.logger.info(whoami() + "received SIGINT/SIGTERM, exiting ...")
        TERMINATED = True


class Interface(Thread):
    def __init__(self, host, ssh_user, ssh_pass, t_token, t_chatid, ping_freq, tbot, if0, logger):
        # interface_ip = pfsense side of modem dchp
        # gateway_ip = ip of modem
        Thread.__init__(self)
        self.daemon = True
        self.logger = logger
        self.running = False
        self.host = host
        self.ssh_user = ssh_user
        self.ssh_pass = ssh_pass
        self.t_token = t_token
        self.t_chatid = t_chatid
        self.ping_freq = ping_freq
        self.tbot = tbot
        self.name = if0["name"]
        self.gateway_ip = if0["gateway_ip"]
        self.interface_ip = if0["interface_ip"]
        self.pfsense_name = if0["pfsense_name"]
        self.status_gateway = 0
        # 1=up & ok / -1=down
        self.status_interface = 0
        self.ssh_connect()
        # ping google dns from pfsense interface
        # !!! -> ssh_user has to be root for this to work on pfsense, not admin !!!
        self.interface_command = "ping -c 1 -W " + str(self.ping_freq) + " -S " + self.interface_ip + " 8.8.8.8"
        self.interface_stop = "/etc/rc.linkup stop " + self.pfsense_name
        self.interface_start = "/etc/rc.linkup start " + self.pfsense_name
        # ping modem from this host
        self.gateway_command = ["ping", "-c", "1", "-W", str(self.ping_freq), self.gateway_ip]

    def ssh_connect(self):
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(self.host, username=self.ssh_user, password=self.ssh_pass)
            self.logger.info(whoami() + "connected to " + self.name + "!")
            return True
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": cannot ssh_connect to " + self.name)
            self.ssh = None
            self.status_interface = 0  # cannot ssh
            return False

    def get_interface_status(self):
        # ping -c 1 -W 5 -S 192.168.2.2 8.8.8.8
        try:
            transport = self.ssh.get_transport()
            transport.send_ignore()
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": " + self.name)
            ret = self.ssh_connect()
            if not ret:
                return 0
        try:
            stdin, stdout, stderr = self.ssh.exec_command(self.interface_command)
            stdout.channel.recv_exit_status()
            resp_stdout = stdout.readlines()
            resp_stderr = stderr.readlines()
            # check if stderr
            err0 = False
            for err in resp_stderr:
                if err:
                    err0 = True 
                    break
            if err0:
                return -1
            # check stdout if ok
            success0 = False
            for std in resp_stdout:
                if "1 packets received" in std:
                    success0 = True
                    break
            if success0:
                return 1
            return -1
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": " + self.name)
            return 0

    def get_gateway_status(self):
        try:
            resp = subprocess.Popen(self.gateway_command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            resp_stdout = resp.stdout.readlines()
            resp_stderr = resp.stderr.readlines()
            err0 = None
            for err in resp_stderr:
                err0 = err.decode("utf-8")
                if err0:
                    reslist[i] = (-1, ip)
                    break
            if err0:
                return -1
            success = False
            for std in resp_stdout:
                std0 = std.decode("utf-8")
                if "1 received" in std0:
                    success = True
                    break
            if success:
                return 1
            return -1
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": " + self.name)
            return 0
        

    def stop(self):
        self.running = False
        self.logger.debug("stopping " + self.name + " thread ...")

    def run(self):
        self.running = True
        while self.running:
            # first ping 8.8.8.8 from pfsense outer interface
            if_status_new = self.get_interface_status()
            self.logger.debug(whoami() + "INTERFACE - " + self.name + ": status = " + str(if_status_new))
            if self.status_interface != if_status_new:
                self.logger.info(whoami() + "INTERFACE - " + self.name + ": status changed, probing max. 3x again ...")
                for _ in range(3):
                    time.sleep(self.ping_freq)
                    if_status_new_new = self.get_interface_status()
                    if if_status_new_new != if_status_new:
                        break
                if if_status_new_new == if_status_new:
                    statusstring = "INTERFACE - " + self.name + ": status changed from " + str(self.status_interface) + " to " + str(if_status_new)
                    self.logger.info(whoami() + statusstring)
                    self.tbot.send(statusstring)
                    self.status_interface = if_status_new
            # only check ping to modem if internet (interface) is up
            # (otherwise maybe modem is restarting etc.)
            if self.status_interface == 1:
                gw_status_new = self.get_gateway_status()
                self.logger.debug(whoami() + "GATEWAY - " + self.name + ": status = " + str(gw_status_new))
                if self.status_gateway != gw_status_new:
                    self.logger.info(whoami() + "GATEWAY - " + self.name + ": status changed, probing max. 3x again ...")
                    for _ in range(3):
                        time.sleep(self.ping_freq)
                        gw_status_new_new = self.get_gateway_status()
                        if gw_status_new_new != gw_status_new:
                            break
                    if gw_status_new_new == gw_status_new:
                        statusstring = "GATEWAY - " + self.name + ": status changed from " + str(self.status_gateway) + " to " + str(gw_status_new)
                        self.logger.info(whoami() + statusstring)
                        self.tbot.send(statusstring)
                        self.status_gateway = gw_status_new
                        if self.status_gateway == -1:
                            self.logger.info(whoami() + "GATEWAY - " + self.name + " is down, restarting ...")
                            self.restart_gateway()
            time.sleep(self.ping_freq)
        self.logger.info(whoami() + "... " + self.name + " thread stopped!")

    def restart_gateway(self):
        connected = False
        for _ in range(3):
            try:
                transport = self.ssh.get_transport()
                transport.send_ignore()
                connected = True
                break
            except Exception as e:
                self.logger.warning(whoami() + str(e) + ": " + self.name)
                ret = self.ssh_connect()
        if not connected:
            self.logger.warning(whoami() + self.name + " cannot establish connection!")
            return -1
        try:
            # first, stop interface
            self.logger.info(whoami() + self.name + " stopping interface ...")
            stdin, stdout, stderr = self.ssh.exec_command(self.interface_stop)
            stdout.channel.recv_exit_status()
            resp_stdout = stdout.readlines()
            resp_stderr = stderr.readlines()
            # check if stderr
            err0 = False
            for err in resp_stderr:
                if err:
                    err0 = True 
                    break
            if err0:
                self.logger.error(whoami() + self.name + ": " + str(err) + " ... cannot stop interface!")
                return -1
            self.logger.info(whoami() + self.name + "interface stopped, now waiting ...")
            # obviously success, now wait
            time.sleep(self.ping_freq * 4)
            # now start
            self.logger.info(whoami() + self.name + " starting interface ...")
            stdin, stdout, stderr = self.ssh.exec_command(self.interface_start)
            stdout.channel.recv_exit_status()
            resp_stdout = stdout.readlines()
            resp_stderr = stderr.readlines()
            # check if stderr
            err0 = False
            for err in resp_stderr:
                if err:
                    err0 = True 
                    break
            if err0:
                self.logger.error(whoami() + self.name + ": " + str(err) + " ... cannot start interface!")
                return -1
            self.logger.info(whoami() + self.name + "interface started, success!")
            return 1
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": " + self.name + " error in interface restart!")
            return -1

class TelegramBot:
    def __init__(self, config, logger):
        self.token = config["t_token"]
        self.chatid = config["t_chatid"]
        self.logger = logger
        self.status = False
        self.setup_bot()

    def setup_bot(self):
        try:
            self.updater = Updater(self.token, use_context=True)
            self.bot = self.updater.bot
            self.logger.info(whoami() + "init telegram bot success!")
            self.status = True
        except Exception as e:
            self.logger.error(whoami() + str(e) + ": cannot init telegram bot!")
            self.status = False

    def stop_bot(self):
        if not self.status:
            return
        self.send("pfintchk telegram bot stopped!")
        self.updater.stop()
        self.logger.info(whoami() + "telegram bot stopped!")

    def send(self, text):
        if not self.status:
            return
        try:
            self.bot.send_message(chat_id=self.chatid, text=text)
        except Exception as e:
            self.logger.warning(whoami() + str(e) + ": chat_id " + str(self.chatid))


def ReadConfig(cfg, logger):
    config = {}
    try:
        config["ssh_user"] = cfg["OPTIONS"]["ssh_user"]
        config["ssh_pass"] = cfg["OPTIONS"]["ssh_pass"]
        config["host"] = cfg["OPTIONS"]["host"]
        config["ping_freq"] = int(cfg["OPTIONS"]["ping_freq"])
        config["t_token"] = cfg["TELEGRAM"]["token"]
        config["t_chatid"] = int(cfg["TELEGRAM"]["chatid"])
    except Exception as e:
        logger.error(whoami() + str(e))
        return None
    config["interfaces"] = []
    idx = 1
    while True:
        try:
            str0 = "INTERFACE" + str(idx)
            name = cfg[str0]["name"]
            interface_ip = cfg[str0]["interface_ip"]
            gateway_ip = cfg[str0]["gateway_ip"]
            pfsense_name = cfg[str0]["pfsense_name"]
            idata = {
                "name": name,
                "pfsense_name": pfsense_name,
                "interface_ip": interface_ip,
                "gateway_ip": gateway_ip
            }
            config["interfaces"].append(idata)
        except Exception:
            break
        idx += 1
    if idx == 1:
        return None
    return config

def run():
    # set up dirs
    userhome = expanduser("~")
    maindir = userhome + "/.pfintchk/"
    if not os.path.exists(maindir):
        return -1

    # set up logger
    logger = logging.getLogger("pfintchk")
    fh = logging.FileHandler(maindir + "pfintchk.log", mode="w")
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)

    # signal handler
    sh = SigHandler(logger)
    signal.signal(signal.SIGINT, sh.sighandler)
    signal.signal(signal.SIGTERM, sh.sighandler)

    # read config file
    try:
        cfg_file = maindir + "pfintchk.cfg"
        cfg = configparser.ConfigParser()
        cfg.read(cfg_file)
        config = ReadConfig(cfg, logger)
        if config:
            logger.info(whoami() + "config read ok!")
        else:
            raise ValueError("error in reading config")
    except Exception as e:
        logger.error(whoami() + str(e))
        return -1

    # telegram
    tbot = TelegramBot(config, logger)
    if not tbot.status:
        logger.error(whoami() + "error in telegram bot set up!")
        return -1
    tbot.send("pfintchk telegram bot started!")

    # setup threads per interface
    threadlist = []
    for if0 in config["interfaces"]:
        thr = Interface(config["host"], config["ssh_user"], config["ssh_pass"], config["t_token"],
                        config["t_chatid"], config["ping_freq"], tbot, if0, logger)
        threadlist.append(thr)
        thr.start()

    # main loop
    while not TERMINATED:
        time.sleep(1)

    # shutdown
    for thr in threadlist:
        thr.stop()
    tbot.stop_bot()
    logger.info(whoami() + " ... exited!")

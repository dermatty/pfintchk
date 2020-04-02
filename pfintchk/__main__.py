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


TERMINATED = False


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


def ping(iplist):
    # -10 not pinged yet, -1 error, 0 failed, 1 success
    reslist = [(ip, -10) for ip in iplist]
    for i, ip in enumerate(iplist):
        command = ['ping', "-c", '1', "-W", "2", ip]
        try:
            resp = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            resp_stdout = resp.stdout.readlines()
            resp_stderr = resp.stderr.readlines()
            err0 = None
            for err in resp_stderr:
                err0 = err.decode("utf-8")
                if err0:
                    reslist[i] = (-1, ip)
                    break
            if err0:
                continue
            success = False
            for std in resp_stdout:
                std0 = std.decode("utf-8")
                if "1 received" in std0:
                    success = True
                    break
            if success:
                reslist[i] = (1, ip)
            else:
                reslist[i] = (0, ip)
        except Exception as e:
            print(str(e))
            reslist[i] = (-1, ip)
    return reslist


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
    logger.setLevel(logging.DEBUG)

    # signal handler
    sh = SigHandler(logger)
    signal.signal(signal.SIGINT, sh.sighandler)
    signal.signal(signal.SIGTERM, sh.sighandler)

    # read config file
    try:
        cfg_file = maindir + "pfintchk.cfg"
        cfg = configparser.ConfigParser()
        cfg.read(cfg_file)
        iplist_raw = cfg["OPTIONS"]["IPLIST"]
        iplist = json.loads(iplist_raw)
        ssh_user = cfg["OPTIONS"]["SSH_USER"]
        ssh_pass = cfg["OPTIONS"]["SSH_PASS"]
        if not iplist:
            print("!!!")
            raise ValueError("invalid / empty ip list!")
        logger.info(whoami() + "config read ok!")
    except Exception as e:
        logger.error(whoami() + str(e))
        return -1

    # main loop
    while not TERMINATED:
        reslist = ping(iplist)
        for resultcode, ip in reslist:
            if resultcode == 0:
                logger.warning(whoami() + ip + ": err code " + str(resultcode) + " - no success, restarting interface!")
                # hier ssh into pfsense & restart interface
                pass
        time.sleep(1)

    logger.info(whoami() + " ... exited!")

#!/usr/bin/env python3
"""
Disparador pontual da automacao WorkLab.
Roda continuamente no PC servidor e aciona o GitHub Actions
nos horarios exatos via workflow_dispatch (sem depender do schedule do GitHub).
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import schedule
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "schedule"])
    import schedule

try:
    from dotenv import load_dotenv
    _env = Path(__file__).parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

SP_TZ  = timezone(timedelta(hours=-3))
TOKEN  = os.getenv("GITHUB_PAT", "")
REPO   = "marcotozicdviver/worklab-automation"
BRANCH = "master"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "scheduler.log",
            encoding="utf-8",
        ),
    ],
)


def _agora() -> str:
    return datetime.now(SP_TZ).strftime("%H:%M BRT")


def trigger_worklab():
    logging.info(f"[{_agora()}] Disparando automacao WorkLab no GitHub Actions...")

    if not TOKEN:
        logging.error("  ERRO: GITHUB_PAT nao configurado no .env!")
        return

    url = (
        f"https://api.github.com/repos/{REPO}"
        f"/actions/workflows/worklab.yml/dispatches"
    )
    body = json.dumps({"ref": BRANCH}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            logging.info(f"  Trigger enviado com sucesso! HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode(errors="replace")
        logging.error(f"  Erro HTTP {e.code}: {body_err}")
    except Exception as e:
        logging.error(f"  Erro ao disparar: {e}")


# Horarios de disparo em BRT (PC deve estar no fuso America/Sao_Paulo)
HORARIOS_BRT = [
    "19:30", "20:30", "21:30", "22:30", "23:30",
    "00:30", "01:30", "02:30", "03:30", "04:30", "05:30",
]

for h in HORARIOS_BRT:
    schedule.every().day.at(h).do(trigger_worklab)

logging.info("=" * 55)
logging.info(f"Scheduler WorkLab iniciado em {datetime.now(SP_TZ).strftime('%Y-%m-%d %H:%M:%S BRT')}")
logging.info(f"Horarios: {', '.join(HORARIOS_BRT)}")
logging.info(f"Token GitHub: {'configurado' if TOKEN else 'AUSENTE — configure GITHUB_PAT no .env'}")
logging.info("=" * 55)

while True:
    schedule.run_pending()
    time.sleep(20)

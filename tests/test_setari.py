"""smtp_config() — migrarea SMTP din .env în setările de operare (secțiunea 16).

Regula: dacă operatorul a salvat un smtp_host în Repository (Firestore, după
migrare), acela câștigă integral față de .env — altfel se folosește .env,
ca la instalarea locală inițială."""
from __future__ import annotations

import dataclasses

import app.config as config_mod
from app import setari as setari_mod
from app.config import MEDIU


def test_fara_setare_salvata_foloseste_env(repo, monkeypatch):
    mediu_activ = dataclasses.replace(
        MEDIU, smtp_host="env.smtp.local", smtp_port=25, smtp_user="env-user",
        smtp_password="env-parola", smtp_from_name="Din .env", smtp_use_tls=False,
    )
    monkeypatch.setattr(config_mod, "MEDIU", mediu_activ)

    smtp = setari_mod.smtp_config(repo)
    assert smtp == {
        "host": "env.smtp.local", "port": 25, "user": "env-user",
        "password": "env-parola", "from_name": "Din .env", "use_tls": False,
    }


def test_setare_salvata_castiga_integral_fata_de_env(repo, monkeypatch):
    mediu_activ = dataclasses.replace(MEDIU, smtp_host="env.smtp.local", smtp_user="env-user",
                                       smtp_password="env-parola")
    monkeypatch.setattr(config_mod, "MEDIU", mediu_activ)

    setari_mod.set(repo, "smtp_host", "firestore.smtp.local")
    setari_mod.set(repo, "smtp_port", 465)
    setari_mod.set(repo, "smtp_user", "operator@firma.ro")
    setari_mod.set(repo, "smtp_password", "parola-din-firestore")
    setari_mod.set(repo, "smtp_from_name", "Prognoză Firma")
    setari_mod.set(repo, "smtp_use_tls", "false")

    smtp = setari_mod.smtp_config(repo)
    assert smtp == {
        "host": "firestore.smtp.local", "port": 465, "user": "operator@firma.ro",
        "password": "parola-din-firestore", "from_name": "Prognoză Firma", "use_tls": False,
    }

from __future__ import annotations

import logging
import random
import re
import string
import time
from pathlib import Path
from typing import Protocol

import httpx

from .config import TranscriptionServiceType

logger = logging.getLogger(__name__)


class TranscriptionService(Protocol):
    """Protocol per als serveis de transcripció."""

    def transcribe(self, audio_path: Path) -> Path | None:
        """Transcriu un fitxer d'àudio i retorna la ruta del fitxer .txt resultant.
        Retorna None si la transcripció falla.
        """
        ...


class SoftCatalaTranscriptionService:
    """Implementació del servei de transcripció mitjançant SoftCatalà i mail.tm."""

    MAIL_API = "https://api.mail.tm"
    SC_API = "https://api.softcatala.org/transcribe-service/v1"

    def _create_email(self) -> tuple[str, str]:
        """Crea un compte mail.tm. Retorna (email, bearer_token)."""
        try:
            domains = httpx.get(f"{self.MAIL_API}/domains", timeout=30.0).json()
            domain = domains["hydra:member"][0]["domain"]

            user = "".join(random.choices(string.ascii_lowercase + string.digits, k=14))
            email = f"{user}@{domain}"
            password = "".join(random.choices(string.ascii_letters + string.digits, k=16)) + "!"

            httpx.post(
                f"{self.MAIL_API}/accounts",
                json={"address": email, "password": password},
                timeout=30.0,
            ).raise_for_status()

            resp = httpx.post(
                f"{self.MAIL_API}/token",
                json={"address": email, "password": password},
                timeout=30.0,
            )
            resp.raise_for_status()
            token = resp.json()["token"]

            return email, token
        except Exception as e:
            logger.error("No s'ha pogut crear el correu temporal: %s", e)
            raise

    def _wait_for_uuid(self, token: str, max_wait_seconds: int = 2400) -> str:
        """Espera el correu de SoftCatalà i n'extreu el UUID."""
        headers = {"Authorization": f"Bearer {token}"}
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                resp = httpx.get(
                    f"{self.MAIL_API}/messages", headers=headers, timeout=30.0
                ).json()
                for msg in resp.get("hydra:member", []):
                    subject = msg.get("subject", "")
                    if "transcri" in subject.lower() or "softcatal" in subject.lower():
                        msg_detail = httpx.get(
                            f"{self.MAIL_API}/messages/{msg['id']}",
                            headers=headers,
                            timeout=30.0,
                        ).json()
                        body = msg_detail.get("text", "") or msg_detail.get("html", [""])[0]
                        match = re.search(r'uuid=([\w-]+)', body)
                        if match:
                            return match.group(1)
            except Exception as e:
                logger.warning("Error comprovant correu: %s", e)

            time.sleep(30)

        raise TimeoutError("No s'ha rebut cap correu després de 40 minuts de marge.")

    def transcribe(self, audio_path: Path) -> Path | None:
        try:
            email, token = self._create_email()
        except Exception:
            return None

        url_upload = f"{self.SC_API}/transcribe_file/"
        logger.info("Demanant transcripció a Softcatalà per %s (correu: %s)", audio_path.name, email)

        try:
            with open(audio_path, "rb") as f:
                data = {"model_name": "medium", "email": email}
                files = {"file": (audio_path.name, f, "audio/mpeg")}
                with httpx.Client(timeout=180.0) as client:
                    response = client.post(url_upload, data=data, files=files)

            if response.status_code not in (200, 201, 202):
                logger.error("Error Softcatalà HTTP %s: %s", response.status_code, response.text)
                return None

            logger.info("Pujada correcta (cua: %s). Esperant correu...", response.json().get("waiting_queue", "?"))
            uuid = self._wait_for_uuid(token)
            logger.info("UUID rebut: %s", uuid)

            # Descarregar el .txt directament per API
            download_url = f"{self.SC_API}/get_file/?uuid={uuid}&ext=txt"
            with httpx.Client(timeout=60.0) as client:
                txt_response = client.get(download_url)
                txt_response.raise_for_status()

            out_txt = audio_path.with_suffix(".txt")
            out_txt.write_bytes(txt_response.content)
            logger.info("Transcripció desada a %s", out_txt.name)
            return out_txt

        except TimeoutError as te:
            logger.error(str(te))
            return None
        except Exception as e:
            logger.error("Excepció transcrivint %s: %s", audio_path.name, e)
            return None


class WhisperLocalTranscriptionService:
    """Transcripció local amb OpenAI Whisper."""

    def __init__(self, model_name: str = "medium", language: str = "ca") -> None:
        self._model_name = model_name
        self._language = language
        self._model = None

    def _get_model(self):
        if self._model is None:
            import whisper
            logger.info("Carregant model Whisper '%s'...", self._model_name)
            self._model = whisper.load_model(self._model_name)
        return self._model

    def transcribe(self, audio_path: Path) -> Path | None:
        try:
            model = self._get_model()
            logger.info("Transcrivint localment %s amb Whisper %s...", audio_path.name, self._model_name)
            result = model.transcribe(str(audio_path), language=self._language)

            out_txt = audio_path.with_suffix(".txt")
            out_txt.write_text(result["text"], encoding="utf-8")
            logger.info("Transcripció desada a %s", out_txt.name)
            return out_txt
        except Exception as e:
            logger.error("Error transcrivint %s amb Whisper: %s", audio_path.name, e)
            return None


class OlamaTranscriptionService:
    """Implementació del servei de transcripció mitjançant Olama local."""

    def transcribe(self, audio_path: Path) -> Path | None:
        # TODO: Implementar interacció amb Olama local
        return None


def get_transcription_service(service_type: TranscriptionServiceType | None) -> TranscriptionService | None:
    """Factoria per obtenir el servei de transcripció adequat segons la configuració."""
    if service_type == TranscriptionServiceType.SoftCatala:
        return SoftCatalaTranscriptionService()
    elif service_type == TranscriptionServiceType.Whisper:
        return WhisperLocalTranscriptionService()
    elif service_type == TranscriptionServiceType.Olama:
        return OlamaTranscriptionService()

    return None

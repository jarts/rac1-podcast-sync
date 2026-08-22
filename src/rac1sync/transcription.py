from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import TranscriptionServiceType


class TranscriptionService(Protocol):
    """Protocol per als serveis de transcripció."""
    
    def transcribe(self, audio_path: Path) -> Path | None:
        """Transcriu un fitxer d'àudio i retorna la ruta del fitxer .txt resultant.
        Retorna None si la transcripció falla.
        """
        ...


import logging
import time
import httpx
import re

logger = logging.getLogger(__name__)


class SoftCatalaTranscriptionService:
    """Implementació del servei de transcripció mitjançant SoftCatalà i 1secmail."""
    
    def _create_email(self) -> tuple[str, str, str]:
        url = "https://www.1secmail.com/api/v1/?action=genRandomMailbox&count=1"
        try:
            resp = httpx.get(url, timeout=30.0).json()
            email = resp[0]
            login, domain = email.split("@")
            return email, login, domain
        except Exception as e:
            logger.error("No s'ha pogut generar el correu: %s", e)
            raise

    def _wait_for_email(self, login: str, domain: str, max_wait_seconds: int = 1200) -> str:
        url_messages = f"https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}"
        start_time = time.time()
        
        while time.time() - start_time < max_wait_seconds:
            try:
                resp = httpx.get(url_messages, timeout=30.0).json()
                if resp and len(resp) > 0:
                    email_id = resp[0]["id"]
                    msg_url = f"https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={email_id}"
                    msg_data = httpx.get(msg_url, timeout=30.0).json()
                    return str(msg_data.get("textBody") or msg_data.get("htmlBody") or "")
            except Exception as e:
                logger.warning("Error comprovant correu: %s", e)
            
            time.sleep(30)
            
        raise TimeoutError("No s'ha rebut cap correu després de 20 minuts de marge.")

    def transcribe(self, audio_path: Path) -> Path | None:
        try:
            email, login, domain = self._create_email()
        except Exception:
            return None
            
        url_upload = "https://api.softcatala.org/transcribe-service/v1/transcribe_file"
        logger.info("Demanant transcripció a Softcatalà per %s (correu temporal: %s)", audio_path.name, email)
        
        try:
            with open(audio_path, "rb") as f:
                data = {"model_name": "medium", "email": email}
                files = {"file": (audio_path.name, f, "audio/mpeg")}
                with httpx.Client(follow_redirects=True, timeout=180.0) as client:
                    response = client.post(url_upload, data=data, files=files)
                    
            if response.status_code not in (200, 201, 202, 307) and not (response.status_code == 404 and "uvicorn" in response.headers.get("server", "").lower()):
                logger.error("Error Softcatalà HTTP %s: %s", response.status_code, response.text)
                return None
                
            logger.info("Pujada correcte. S'espera el correu a %s (cada 30s)...", email)
            msg_body = self._wait_for_email(login, domain)
            
            # 4) Recuperar URL de resultats
            # Patró basat en el text: "L'adreça és https://www.softcatala.org/transcripcio/resultats/?uuid=X"
            match = re.search(r'(https://www\.softcatala\.org/transcripcio/resultats/\?uuid=[\w-]+)', msg_body)
            if not match:
                logger.error("No s'ha trobat la URL de resultats al correu de Softcatalà.")
                logger.debug("Cos del correu: %s", msg_body[:300])
                return None
                
            results_url = match.group(1)
            logger.info("Sol·licitant URL de resultats: %s", results_url)
            
            # 5, 6, 7) Visitar pàgina de resultats, buscar link amb 'text amb la transcripció'
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                res_page = client.get(results_url)
                
                # Cerquem el valor 'href=' que es trobi a prop del text del link de la descarrega
                # Un procés possible: <a href="https://api.../download..." class="...">[possible html incrustat]text amb la transcripció[possible html incrustat]</a>
                link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(?:(?!</a>).)*?text amb la transcripció(?:(?!</a>).)*?</a>', res_page.text, re.IGNORECASE | re.DOTALL)
                
                if not link_match:
                    logger.warning("Intent alternatiu de trobar el fitxer .txt a la resposta")
                    link_match = re.search(r'href=["\'](https://api\.softcatala\.org/[^"\']+txt)["\']', res_page.text)
                
                if not link_match:
                    logger.error("No s'ha trobat l'enllaç de descàrrega a la pàgina de resultats. HTML: %s", res_page.text[:500])
                    return None
                    
                download_url = link_match.group(1)
                logger.info("Descarregant la transcripció des de %s", download_url)
                
                txt_response = client.get(download_url)
                txt_response.raise_for_status()
                
                # Extraiem el nom original amb què ens ho vol descarregar el servidor HTTP
                filename = None
                cd = txt_response.headers.get("content-disposition", "")
                if "filename=" in cd:
                    match_cd = re.search(r'filename=["\']?([^"\';]+)["\']?', cd)
                    if match_cd:
                        filename = match_cd.group(1)
                        
                if not filename:
                    # Intenta deduir-ho des de la URL excloent paràmetres GET
                    url_name = download_url.split("?")[0].split("/")[-1]
                    if url_name and "." in url_name:
                        filename = url_name
                    else:
                        filename = f"transcripcio_{audio_path.name}.txt"
                
                out_txt = audio_path.parent / filename
                with open(out_txt, "wb") as out:
                    out.write(txt_response.content)
                    
                logger.info("Transcripció desada correctament a %s", out_txt.name)
                return out_txt
                
        except TimeoutError as te:
            logger.error(str(te))
            return None
        except Exception as e:
            logger.error("Excepció transcrivint Softcatalà per %s: %s", audio_path.name, e)
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
    elif service_type == TranscriptionServiceType.Olama:
        return OlamaTranscriptionService()
    
    return None

import os
import re
import json
import logging
import urllib.parse
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
import requests
from bs4 import BeautifulSoup

# Configuración profesional del sistema de registros
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Carga segura de variables de entorno y secretos de infraestructura
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def build_scraperapi_url(target_url):
    """
    Construye de manera estricta la URL de conexión a través de la pasarela proxy
    de ScraperAPI utilizando el patrón paramétrico exigido.
    """
    if not SCRAPER_API_KEY:
        logging.warning("SCRAPER_API_KEY no configurada. Intentando conexion directa para: %s", target_url)
        return target_url
    return f"http://scraperapi.com?api_key={SCRAPER_API_KEY}&url={urllib.parse.quote(target_url)}"

def clean_text(text):
    """Normaliza texto removiendo diacríticos y homogeneizando codificación."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    cleaned = "".join([c for c in text if unicodedata.category(c) != "Mn"])
    return cleaned.lower().strip()

def normalize_team_name(name):
    """
    Aplica una normalización avanzada eliminando ruidos y palabras de parada
    comunes en la denominación de entidades de clubes deportivos de fútbol.
    """
    name = clean_text(name)
    name = re.sub(r"[^\w\s]", "", name)  # Limpieza de signos especiales
    
    # Lista de exclusión lexicográfica estructurada para normalizar variantes comunes de nombres
    noise_patterns = [
        r"\bfc\b", r"\bcf\b", r"\bcd\b", r"\bsd\b", r"\bca\b", r"\bfk\b", 
        r"\bsc\b", r"\bac\b", r"\breal\b", r"\bunited\b", r"\butd\b", 
        r"\bcity\b", r"\btown\b", r"\bclub\b", r"\bde\b", r"\bsporting\b", 
        r"\bfutbol\b", r"\bfutebol\b", r"\bsoccer\b", r"\bathletic\b", r"\batletico\b"
    ]
    
    for pattern in noise_patterns:
        name = re.sub(pattern, "", name)
        
    return re.sub(r"\s+", " ", name).strip()

def calculate_fuzzy_match(team_a, team_b, threshold=0.75):
    """
    Evalúa la correspondencia de nombres de equipos deportivos mediante distancia
    de caracteres basada en el algoritmo Ratcliff-Obershelp.
    """
    norm_a = normalize_team_name(team_a)
    norm_b = normalize_team_name(team_b)
    
    if not norm_a or not norm_b:
        return False, 0.0
        
    if norm_a == norm_b or norm_a in norm_b or norm_b in norm_a:
        return True, 1.0
        
    similarity = SequenceMatcher(None, norm_a, norm_b).ratio()
    return similarity >= threshold, similarity

def query_espn_fixtures():
    """
    Interroga de forma directa y keyless la API de scores de ESPN para múltiples ligas de fútbol.
    Construye el conjunto de datos de validación para cotejar la vigencia de los eventos.
    """
    logging.info("Consultando la API publica de ESPN para validacion de fixtures...")
    fixtures = []
    
    # Lista de ligas de alto perfil para mapeo geográfico de partidos del día
    leagues = ["eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "usa.1", "mex.1", "por.1", "ned.1", "uefa.champions"]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for league in leagues:
        api_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
        try:
            response = requests.get(api_url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for event in data.get("events", []):
                    competitions = event.get("competitions", [{}])
                    if competitions:
                        competitors = competitions[0].get("competitors", [])
                        home = ""
                        away = ""
                        for competitor in competitors:
                            if competitor.get("homeAway") == "home":
                                home = competitor.get("team", {}).get("displayName", "")
                            elif competitor.get("homeAway") == "away":
                                away = competitor.get("team", {}).get("displayName", "")
                        if home and away:
                            fixtures.append({
                                "home_team": home,
                                "away_team": away,
                                "league": league,
                                "match_date": event.get("date", "")
                            })
        except Exception as e:
            logging.error("Fallo controlado en consulta ESPN para la liga %s: %s", league, str(e))
            continue
            
    logging.info("Se han validado %d partidos con la API publica de ESPN.", len(fixtures))
    return fixtures

def scrape_forebet_predictions():
    """
    Raspa el portal de predicciones matemáticas de hoy de Forebet.
    Extrae probabilidades estimadas para mercados tradicionales 1X2.
    """
    logging.info("Iniciando el proceso de raspado en Forebet...")
    predictions = []
    target_url = "https://www.forebet.com/en/predictions-1x2"
    proxied_url = build_scraperapi_url(target_url)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        response = requests.get(proxied_url, headers=headers, timeout=30)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            rows = soup.find_all("div", class_=lambda x: x and "rcnt" in x)
            
            for row in rows:
                try:
                    home_elem = row.find("span", class_="homeTeam")
                    away_elem = row.find("span", class_="awayTeam")
                    if not home_elem or not away_elem:
                        continue
                        
                    home_team = home_elem.get_text(strip=True)
                    away_team = away_elem.get_text(strip=True)
                    
                    prob_spans = row.find_all("span", class_="fprt")
                    if len(prob_spans) >= 3:
                        # Conversion automatica de porcentajes de mercado a formato decimal continuo
                        p_home = float(prob_spans[0].get_text(strip=True).replace("%", "")) / 100.0
                        p_draw = float(prob_spans[1].get_text(strip=True).replace("%", "")) / 100.0
                        p_away = float(prob_spans[2].get_text(strip=True).replace("%", "")) / 100.0
                        
                        predictions.append({
                            "home_team": home_team,
                            "away_team": away_team,
                            "prob_home": p_home,
                            "prob_draw": p_draw,
                            "prob_away": p_away
                        })
                except Exception as inner_err:
                    logging.debug("Error procesando registro individual de Forebet: %s", str(inner_err))
                    continue
        else:
            logging.error("Forebet respondio con un codigo de error HTTP: %d", response.status_code)
    except Exception as e:
        logging.error("Fallo catastrófico al procesar Forebet: %s", str(e))
        
    logging.info("Forebet raspado con éxito. Partidos procesados: %d", len(predictions))
    return predictions

def scrape_oddspedia_odds():
    """
    Raspa la sección de partidos de fútbol en Oddspedia para extraer cuotas de mercado.
    Implementa un parser semántico genérico altamente resistente a cambios estructurales de clases.
    """
    logging.info("Iniciando el proceso de raspado en Oddspedia con motor proxy...")
    odds_data = []
    target_url = "https://oddspedia.com/football"
    # Oddspedia requiere renderizado JS para desplegar datos reactivos en el DOM plano
    proxied_url = build_scraperapi_url(target_url) + "&render=true"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    
    try:
        response = requests.get(proxied_url, headers=headers, timeout=45)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Buscar contenedores representativos de eventos deportivos (clases que contienen "event", "match", "card" o "row")
            event_blocks = soup.find_all(['div', 'tr', 'li'], class_=lambda c: c and any(k in c.lower() for k in ["match", "event", "card", "row"]))
            
            for block in event_blocks:
                try:
                    # Extracción heurística de textos candidatos a nombres de equipos
                    name_elems = block.find_all(class_=lambda c: c and any(k in c.lower() for k in ["team", "participant", "title", "name"]))
                    teams = [n.get_text(strip=True) for n in name_elems if n.get_text(strip=True)]
                    teams = list(dict.fromkeys(teams))  # Preserva orden eliminando duplicados
                    
                    if len(teams) >= 2:
                        home, away = teams[0], teams[1]
                        
                        # Escaneo y parsing de cuotas numéricas decimales adyacentes al bloque del partido
                        odds_candidates = block.find_all(class_=lambda c: c and any(k in c.lower() for k in ["odds", "odd-value", "btn", "price"]))
                        odds = []
                        for candidate in odds_candidates:
                            txt = candidate.get_text(strip=True)
                            try:
                                val = float(txt)
                                if 1.01 < val < 50.0:  # Rango razonable para cuotas de fútbol en mercado 1X2
                                    odds.append(val)
                            except ValueError:
                                continue
                                
                        if len(odds) >= 3:
                            odds_data.append({
                                "home_team": home,
                                "away_team": away,
                                "odds_home": odds[0],
                                "odds_draw": odds[1],
                                "odds_away": odds[2]
                            })
                except Exception as inner_err:
                    logging.debug("Error procesando bloque individual de Oddspedia: %s", str(inner_err))
                    continue
        else:
            logging.error("Oddspedia respondio con codigo HTTP inusual: %d", response.status_code)
    except Exception as e:
        logging.error("Error no controlado durante la ejecucion en Oddspedia: %s", str(e))
        
    logging.info("Oddspedia procesado con éxito. Cuotas extraídas: %d", len(odds_data))
    return odds_data

def transmit_telegram_broadcast(message):
    """
    Envía mensajes de alerta de manera limpia e interactiva a la API oficial de Telegram.
    Soporta formato HTML nativo y maneja fallos de tokenización de manera segura.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("Notificacion de Telegram omitida: credenciales insuficientes o ausentes.")
        return
        
    api_endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(api_endpoint, json=payload, timeout=15)
        if response.status_code == 200:
            logging.info("Notificacion de Telegram transmitida exitosamente.")
        else:
            logging.error("Error transmitiendo notificacion de Telegram. Codigo: %d", response.status_code)
    except Exception as e:
        logging.error("Excepcion de red controlada al intentar contactar con la API de Telegram: %s", str(e))

def main():
    logging.info("=== INICIANDO SISTEMA AUTOMATIZADO DE TRADING DEPORTIVO ===")
    
    # Ingesta en paralelo secuencial controlado para evitar saturación de red
    espn_db = query_espn_fixtures()
    forebet_db = scrape_forebet_predictions()
    oddspedia_db = scrape_oddspedia_odds()
    
    final_opportunities = []
    aligned_matches_count = 0
    
    # Emparejamiento sistemático cruzado
    for pred in forebet_db:
        f_home = pred["home_team"]
        f_away = pred["away_team"]
        
        # Intentar localizar cuotas equivalentes en Oddspedia
        matched_odds = None
        for odds in oddspedia_db:
            is_home_match, _ = calculate_fuzzy_match(f_home, odds["home_team"])
            is_away_match, _ = calculate_fuzzy_match(f_away, odds["away_team"])
            
            if is_home_match and is_away_match:
                matched_odds = odds
                break
                
        if matched_odds:
            aligned_matches_count += 1
            
            # Buscar validación de vigencia del fixture contra la API publica de ESPN
            is_verified = False
            for fixture in espn_db:
                is_home_espn, _ = calculate_fuzzy_match(f_home, fixture["home_team"])
                is_away_espn, _ = calculate_fuzzy_match(f_away, fixture["away_team"])
                if is_home_espn and is_away_espn:
                    is_verified = True
                    break
                    
            # Evaluaciones cuantitativas del Edge matemático
            prob_matrix = [pred["prob_home"], pred["prob_draw"], pred["prob_away"]]
            odds_matrix = [matched_odds["odds_home"], matched_odds["odds_draw"], matched_odds["odds_away"]]
            outcomes = ["1", "X", "2"]
            
            for idx in range(3):
                p = prob_matrix[idx]
                o = odds_matrix[idx]
                edge_val = (p * o) - 1.0
                
                # Clasificación de apuestas bajo el umbral mínimo del 2%
                if edge_val > 0.02:
                    final_opportunities.append({
                        "home_team": f_home,
                        "away_team": f_away,
                        "market_selection": outcomes[idx],
                        "estimated_probability": round(p, 4),
                        "market_odds": round(o, 2),
                        "mathematical_edge": round(edge_val, 4),
                        "espn_fixture_verified": is_verified,
                        "timestamp_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    })

    # Ordenamiento de mayor a menor según el nivel de Edge detectado en mercado
    final_opportunities = sorted(final_opportunities, key=lambda x: x["mathematical_edge"], reverse=True)
    
    # Consolidación del JSON estructurado de salida
    json_output = {
        "execution_date_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "total_aligned_fixtures": aligned_matches_count,
        "total_opportunities_found": len(final_opportunities),
        "value_bets": final_opportunities
    }
    
    # Escritura atómica a disco para persistencia Git
    try:
        with open("value_bets.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, indent=4, ensure_ascii=False)
        logging.info("Resultados persistidos exitosamente en 'value_bets.json'.")
    except Exception as e:
        logging.error("Error persistiendo datos en formato JSON a disco: %s", str(e))
        
    # Construcción de la alerta estructurada en HTML para distribución en Telegram
    if final_opportunities:
        alert_body = "⚽ <b>ALERTA DE TRADING DEPORTIVO DE VALOR</b> 📊\n"
        alert_body += f"<i>Ejecución: {json_output['execution_date_utc']} UTC</i>\n"
        alert_body += f"Mapeos Exitosos: {aligned_matches_count} | Oportunidades: {len(final_opportunities)}\n"
        alert_body += "===================================\n\n"
        
        # Limitar la salida a los mejores 12 resultados para evitar límites de envío por mensaje de Telegram
        for i, bet in enumerate(final_opportunities[:12]):
            verification_status = "✅ ESPN Confirmado" if bet["espn_fixture_verified"] else "⚠️ No validado en ESPN"
            alert_body += f"{i+1}. <b>{bet['home_team']} vs {bet['away_team']}</b>\n"
            alert_body += f"   • Pronostico: <b>Seleccion {bet['market_selection']}</b>\n"
            alert_body += f"   • Probabilidad: {round(bet['estimated_probability'] * 100, 2)}%\n"
            alert_body += f"   • Cuota de Mercado: <b>{bet['market_odds']}</b>\n"
            alert_body += f"   • <b>Edge Neto: +{round(bet['mathematical_edge'] * 100, 2)}%</b>\n"
            alert_body += f"   • Fixture: <i>{verification_status}</i>\n"
            alert_body += "-----------------------------------\n"
            
        transmit_telegram_broadcast(alert_body)
    else:
        logging.info("No se han detectado ineficiencias de mercado de valor positivo en este ciclo.")

if __name__ == "__main__":
    main()

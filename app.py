from flask import Flask, jsonify, render_template, request
import requests
import os
from dotenv import load_dotenv
from openai import OpenAI
import json
from datetime import date
import feedparser
import urllib.parse
import time
from datetime import datetime
import pytz
import yfinance as yf
import warnings
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from tickers import TICKERS
from flask import Response, stream_with_context

load_dotenv()

app = Flask(__name__)

GROQ_KEY = os.getenv("GROQ_KEY")
client = OpenAI(
    api_key=GROQ_KEY,
    base_url="https://api.groq.com/openai/v1"
)

def geocode_city(city, state=""):
    try:
        query = f"{city} {state}".strip()
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=10
        )
        results = r.json().get("results", [])
        if results:
            return results[0]["latitude"], results[0]["longitude"], results[0].get("country_code", "US")
        return 35.7915, -78.7811, "US"
    except:
        return 35.7915, -78.7811, "US"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/weather")
def weather():
    location_response = requests.get("https://ipinfo.io/json")
    location_data = location_response.json()

    city_param = request.args.get("city", "")
    state_param = request.args.get("state", "")

    if city_param:
        lat, lon, country_code = geocode_city(city_param, state_param)
        CITY = city_param
        COUNTRY = country_code.upper()
    else:
        CITY = "Cary"
        lat = 35.7915
        lon = -78.7811
        COUNTRY = location_data.get("country", "US").upper()

    POSTAL = "27519"

    if COUNTRY == "US":
        from noaa_sdk import NOAA
        noaa = NOAA()

        observations = noaa.get_observations(POSTAL, "US")
        current = next(observations)
        temp = round((current["temperature"]["value"] * 9/5) + 32, 1) if current["temperature"]["value"] else "N/A"
        feels_like = round((current["windChill"]["value"] * 9/5) + 32, 1) if current["windChill"]["value"] else temp
        humidity = round(current["relativeHumidity"]["value"], 1) if current["relativeHumidity"]["value"] else "N/A"
        description = current.get("textDescription") or None

        forecasts = noaa.points_forecast(lat, lon, hourly=False)
        periods = forecasts["properties"]["periods"][:10]
        if not description:
            description = periods[0].get("shortForecast", "N/A")
        rain_chance = periods[0].get("probabilityOfPrecipitation", {}).get("value", 0) or 0

        forecast_days = []
        for i in range(0, min(10, len(periods)), 2):
            day_period = periods[i]
            night_period = periods[i+1] if i+1 < len(periods) else periods[i]
            label = "Today" if day_period["name"] in ["Today", "This Afternoon"] else day_period["name"]
            forecast_days.append({
                "day": label,
                "high": day_period["temperature"],
                "low": night_period["temperature"],
                "desc": day_period["shortForecast"],
                "rain": day_period.get("probabilityOfPrecipitation", {}).get("value", 0) or 0
            })

    else:
        from openmeteo_requests import Client as OMClient
        import requests_cache
        from retry_requests import retry

        cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        openmeteo = OMClient(session=retry_session)

        params = {
            "latitude": lat,
            "longitude": lon,
            "current": ["temperature_2m", "relative_humidity_2m", "apparent_temperature", "weather_code", "precipitation_probability"],
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "weather_code"],
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 6
        }

        responses = openmeteo.weather_api("https://api.open-meteo.com/v1/forecast", params=params)
        om = responses[0]
        cur = om.Current()

        temp = round(cur.Variables(0).Value(), 1)
        humidity = round(cur.Variables(1).Value(), 1)
        feels_like = round(cur.Variables(2).Value(), 1)
        rain_chance = round(cur.Variables(4).Value())

        wmo = {0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
               45:"Foggy",51:"Light drizzle",61:"Light rain",63:"Rain",
               65:"Heavy rain",71:"Light snow",73:"Snow",80:"Rain showers",
               95:"Thunderstorm"}
        description = wmo.get(int(cur.Variables(3).Value()), "Unknown")

        daily = om.Daily()
        import datetime
        forecast_days = []
        for i in range(1, 6):
            forecast_days.append({
                "day": (datetime.datetime.now() + datetime.timedelta(days=i)).strftime("%A"),
                "high": round(daily.Variables(0).ValuesAsNumpy()[i]),
                "low": round(daily.Variables(1).ValuesAsNumpy()[i]),
                "rain": round(daily.Variables(2).ValuesAsNumpy()[i]),
                "desc": wmo.get(int(daily.Variables(3).ValuesAsNumpy()[i]), "Unknown")
            })

    return jsonify({
        "city": CITY,
        "temperature": temp,
        "feels_like": feels_like,
        "humidity": humidity,
        "condition": description,
        "rain_chance": rain_chance,
        "forecast": forecast_days
    })

LOCAL_TEAMS = {
    "NC": ["carolina hurricanes", "carolina panthers", "charlotte hornets", "nc state", "duke", "tar heel", "unc"],
    "NY": ["yankees", "mets", "knicks", "nets", "giants", "jets", "rangers", "islanders", "buffalo bills", "sabres"],
    "CA": ["lakers", "clippers", "warriors", "dodgers", "padres", "giants", "49ers", "rams", "chargers", "kings", "ducks", "angels", "athletics"],
    "TX": ["dallas cowboys", "texans", "rangers", "astros", "mavericks", "rockets", "spurs", "stars", "fc dallas"],
    "FL": ["miami heat", "magic", "marlins", "rays", "buccaneers", "dolphins", "jaguars", "inter miami"],
    "IL": ["bulls", "cubs", "white sox", "bears", "blackhawks"],
    "MA": ["celtics", "red sox", "patriots", "bruins"],
    "PA": ["eagles", "phillies", "sixers", "flyers", "steelers", "pirates", "penguins"],
    "OH": ["cavaliers", "browns", "guardians", "bengals", "reds", "blue jackets"],
    "WA": ["seahawks", "mariners", "kraken", "sounders"],
    "GA": ["hawks", "braves", "falcons", "atlanta united"],
    "AZ": ["suns", "cardinals", "diamondbacks"],
    "CO": ["nuggets", "avalanche", "broncos", "rockies"],
    "MI": ["pistons", "red wings", "lions", "tigers"],
    "MN": ["timberwolves", "wild", "vikings", "twins"],
    "NV": ["raiders", "golden knights", "aces", "vegas"],
    "TN": ["titans", "predators", "grizzlies"],
    "IN": ["pacers", "colts"],
    "MO": ["blues", "cardinals", "chiefs", "royals"],
    "WI": ["bucks", "packers", "brewers"],
    "VA": ["commanders", "capitals", "wizards", "nationals"],
    "MD": ["ravens", "orioles"],
    "DC": ["commanders", "capitals", "wizards", "nationals"],
    "UT": ["jazz"],
    "OK": ["thunder"],
    "LA": ["pelicans", "saints"],
    "OR": ["trail blazers", "timbers"],
    "SC": ["carolina panthers", "carolina hurricanes"],
    "KY": ["louisville", "kentucky"],
    "AL": ["alabama", "auburn"],
}

NATIONAL_FAVORITES = [
    "dallas cowboys", "new england patriots", "kansas city chiefs",
    "green bay packers", "pittsburgh steelers", "san francisco 49ers",
    "los angeles lakers", "boston celtics", "golden state warriors",
    "chicago bulls", "miami heat", "new york knicks",
    "new york yankees", "los angeles dodgers", "boston red sox",
    "chicago cubs", "atlanta braves",
    "toronto maple leafs", "montreal canadiens",
]

ALWAYS_CHECK = [
    ("basketball", "nba"),
    ("football", "nfl"),
    ("baseball", "mlb"),
    ("hockey", "nhl"),
    ("soccer", "fifa.world"),
    ("soccer", "usa.1"),
    ("racing", "f1"),
    ("basketball", "mens-college-basketball"),
]

@app.route("/api/perspective")
def perspective():
    city = request.args.get("city", "Cary")
    state = request.args.get("state", "NC")

    eastern = pytz.timezone("US/Eastern")
    now_et = datetime.now(eastern)
    hour = now_et.hour
    time_of_day = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"

    context_parts = []

    # 1. Weather
    try:
        lat, lon, _ = geocode_city(city, state)
        from openmeteo_requests import Client as OMClient
        import requests_cache
        from retry_requests import retry
        cache_session = requests_cache.CachedSession('.cache', expire_after=1800)
        retry_session = retry(cache_session, retries=2, backoff_factor=0.2)
        om = OMClient(session=retry_session)
        om_params = {
            "latitude": lat, "longitude": lon,
            "current": ["temperature_2m", "weather_code", "precipitation_probability"],
            "temperature_unit": "fahrenheit", "timezone": "auto"
        }
        om_resp = om.weather_api("https://api.open-meteo.com/v1/forecast", params=om_params)
        cur = om_resp[0].Current()
        temp = round(cur.Variables(0).Value(), 1)
        wmo = {0:"clear skies", 1:"mostly clear", 2:"partly cloudy", 3:"overcast",
               45:"foggy", 51:"light drizzle", 61:"light rain", 63:"rain",
               65:"heavy rain", 71:"light snow", 73:"snow", 80:"rain showers", 95:"thunderstorms"}
        desc = wmo.get(int(cur.Variables(1).Value()), "")
        rain = round(cur.Variables(2).Value())
        wx = f"{temp}°F and {desc}"
        if rain > 50:
            wx += f" with {rain}% chance of rain"
        context_parts.append(f"Weather in {city}: {wx}")
    except:
        pass

    # 2. Markets
    try:
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        warnings.filterwarnings("ignore")
        spy_info = yf.Ticker("SPY").fast_info
        spy_pct = round((spy_info.last_price - spy_info.previous_close) / spy_info.previous_close * 100, 2)

        def qfetch(ticker):
            try:
                info = yf.Ticker(ticker).fast_info
                pct = round((info.last_price - info.previous_close) / info.previous_close * 100, 2)
                return {"ticker": ticker, "pct": pct}
            except:
                return None

        with ThreadPoolExecutor(max_workers=8) as ex:
            movers = [r for r in ex.map(qfetch, TICKERS[:40]) if r]
        movers.sort(key=lambda x: abs(x["pct"]), reverse=True)

        if now_et.weekday() >= 5:
            mkt = f"markets closed for the weekend, SPY ended {'+' if spy_pct >= 0 else ''}{spy_pct}% Friday"
        elif hour >= 16:
            mkt = f"markets closed, SPY finished {'+' if spy_pct >= 0 else ''}{spy_pct}%"
        elif hour < 9 or (hour == 9 and now_et.minute < 30):
            mkt = f"markets not open yet, SPY was {'+' if spy_pct >= 0 else ''}{spy_pct}% yesterday"
        else:
            mkt = f"markets open, SPY {'+' if spy_pct >= 0 else ''}{spy_pct}%"

        if movers and abs(movers[0]["pct"]) > 4:
            mkt += f", {movers[0]['ticker']} {'+' if movers[0]['pct'] >= 0 else ''}{movers[0]['pct']}%"
        context_parts.append(f"Markets: {mkt}")
    except:
        pass

    # 3. Headlines — always include World Cup search since ESPN endpoint may be unreliable
    try:
        gq = urllib.parse.quote("major news today 2026")
        lq = urllib.parse.quote(f"{city} {state} news today")
        wcq = urllib.parse.quote("FIFA World Cup 2026 today results score")
        g_feed = feedparser.parse(f"https://news.google.com/rss/search?q={gq}&hl=en-US&gl=US&ceid=US:en")
        l_feed = feedparser.parse(f"https://news.google.com/rss/search?q={lq}&hl=en-US&gl=US&ceid=US:en")
        wc_feed = feedparser.parse(f"https://news.google.com/rss/search?q={wcq}&hl=en-US&gl=US&ceid=US:en")
        heads = [e.title for e in g_feed.entries[:2]] + [e.title for e in l_feed.entries[:1]]
        wc_heads = [e.title for e in wc_feed.entries[:2]]
        if wc_heads:
            context_parts.append(f"FIFA World Cup 2026 headlines: {'; '.join(wc_heads)}")
        if heads:
            context_parts.append(f"Top headlines: {'; '.join(heads)}")
    except:
        pass

    # 4. Sports — check all major leagues, tag by significance and local relevance
    local_keywords = [kw.lower() for kw in LOCAL_TEAMS.get(state.upper(), [])]
    sports_lines = []

    for sport, slug in ALWAYS_CHECK:
        try:
            data = requests.get(
                f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard",
                timeout=5
            ).json()
            for ev in data.get("events", [])[:4]:
                comp = ev.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) < 2:
                    continue
                home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                home_name = home.get("team", {}).get("displayName", "")
                away_name = away.get("team", {}).get("displayName", "")
                home_score = home.get("score", "")
                away_score = away.get("score", "")
                detail = ev.get("status", {}).get("type", {}).get("shortDetail", "")
                season_type = comp.get("type", {}).get("abbreviation", "")
                notes = comp.get("notes", [])
                note = notes[0].get("headline", "") if notes else ""
                event_text = (ev.get("name", "") + " " + note).lower()
                home_record = home.get("records", [{}])[0].get("summary", "") if home.get("records") else ""
                away_record = away.get("records", [{}])[0].get("summary", "") if away.get("records") else ""
                score_str = f"{away_score}-{home_score}" if home_score and away_score else "upcoming"
                record_str = f"({away_record} vs {home_record})" if home_record and away_record else ""

                is_world_cup = slug == "fifa.world" or "world cup" in event_text or "fifa" in event_text
                is_finals = is_world_cup or any(w in event_text for w in ["finals", "championship", "world series", "super bowl"])
                is_playoff = season_type == "POST" or any(w in event_text for w in ["playoff", "semifinal", "quarterfinal", "wild card", "conference"])
                is_local = any(kw in home_name.lower() or kw in away_name.lower() for kw in local_keywords)
                is_national = any(kw in home_name.lower() or kw in away_name.lower() for kw in NATIONAL_FAVORITES)

                if not (is_finals or is_playoff or is_local or is_national):
                    continue

                if is_finals:
                    tag = "[FINALS]"
                elif is_playoff:
                    tag = "[PLAYOFF]"
                elif is_local:
                    tag = "[LOCAL]"
                else:
                    tag = "[POPULAR]"

                line = f"{tag} {note or slug.upper()}: {away_name} vs {home_name} {score_str} {record_str} ({detail})"
                sports_lines.append(line)
        except:
            pass

    if sports_lines:
        context_parts.append("Sports today:\n" + "\n".join(sports_lines[:8]))

    context = "\n".join(context_parts) if context_parts else "No live data available."

    prompt = (
        f"Give someone in {city}, {state} their {time_of_day} read. "
        f"Data:\n\n{context}\n\n"
        f"RULES: Exactly 2 sentences. Not 3. If you write 3, delete the last one. "
        f"Pick the 2 most important things total — ignore everything else completely. "
        f"Priority: FIFA World Cup > [FINALS] > [PLAYOFF] > [LOCAL] > big news > markets > weather. "
        f"World Cup is the biggest sporting event on earth right now — if there are games today, lead with it. "
        f"Use real details: teams, scores, series standing. Not generic phrases like 'sports fans are eagerly awaiting'. "
        f"No filler. No 'meanwhile'. No questions. No emojis. "
        f"GOOD: 'France beat Spain 2-1 to reach the World Cup final — Argentina waits for them Sunday. Knicks tied the NBA Finals 3-3, Game 7 Tuesday.' "
        f"BAD: 'The NBA Finals are headed to Game 6, and sports fans are eagerly awaiting tonight\\'s games.'"
    )

    chat = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}]
    )
    return jsonify({
        "perspective": chat.choices[0].message.content.strip(),
        "context": context
    })

@app.route("/api/chat", methods=["POST"])
def chat_endpoint():
    data = request.json
    message = data.get("message", "").strip()
    context = data.get("context", "")
    history = data.get("history", [])
    city = data.get("city", "Cary")
    state = data.get("state", "NC")

    if not message:
        return jsonify({"response": ""}), 400

    extra_context = ""
    try:
        search_q = urllib.parse.quote(f"{message} {city} {state}")
        general_q = urllib.parse.quote(f"{message} 2026")
        local_feed = feedparser.parse(
            f"https://news.google.com/rss/search?q={search_q}&hl=en-US&gl=US&ceid=US:en"
        )
        general_feed = feedparser.parse(
            f"https://news.google.com/rss/search?q={general_q}&hl=en-US&gl=US&ceid=US:en"
        )

        entries = local_feed.entries[:2] + general_feed.entries[:2]
        articles = []

        for entry in entries[:4]:
            title = entry.title
            pub = entry.get("published", "")
            try:
                raw_url = entry.get("link", "")
                redirect = requests.get(raw_url, timeout=5, allow_redirects=True)
                real_url = redirect.url
                article_resp = requests.get(real_url, timeout=6, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"
                })
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(article_resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    tag.decompose()
                paragraphs = [p.get_text().strip() for p in soup.find_all("p") if len(p.get_text().strip()) > 60]
                article_text = " ".join(paragraphs[:8])[:1500]
                if article_text:
                    articles.append(f"SOURCE: {title} ({pub})\n{article_text}")
                else:
                    articles.append(f"{title} ({pub}): {entry.get('summary','')[:400]}")
            except:
                articles.append(f"{title} ({pub}): {entry.get('summary','')[:400]}")

        if articles:
            extra_context = "\n\nFull article content retrieved for this question:\n\n" + "\n\n---\n\n".join(articles)
    except:
        pass

    messages = [{
        "role": "system",
        "content": (
            f"You are a sharp daily briefing assistant for someone in {city}, {state}. "
            f"You have two sources — use both:\n\n"
            f"1. Today's briefing data:\n{context}\n\n"
            f"2. Full article content pulled live for this question:{extra_context}\n\n"
            f"Answer using specific details from the articles above. "
            f"Names, dates, scores, quotes — use whatever is in there. "
            f"Be direct and conversational. 2-3 sentences unless they ask for more. No emojis."
        )
    }]

    for h in history[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        max_tokens=500
    )

    return jsonify({"response": response.choices[0].message.content.strip()})

@app.route("/api/news")
def news():
    location_response = requests.get("https://ipinfo.io/json")
    location_data = location_response.json()
    CITY = "Cary"
    STATE = "North Carolina"

    def fetch_feed(url, max_items=5):
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:max_items]:
            user_tz = pytz.timezone(location_data.get("timezone", "UTC"))
            raw_date = entry.get("published", None)
            if raw_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(raw_date)
                    pub_date = dt.astimezone(user_tz).strftime("%A, %B %-d %Y · %-I:%M %p %Z")
                except:
                    pub_date = raw_date
            else:
                pub_date = "Date unknown"
            results.append(f"{entry.title} [{pub_date}]: {entry.get('summary', '')[:200]}")
        return results

    world_query    = urllib.parse.quote("world news today")
    us_query       = urllib.parse.quote("US news today")
    business_query = urllib.parse.quote(f"business finance economy stock market {datetime.now().strftime('%B %Y')}")
    tech_query     = urllib.parse.quote("technology AI news today")
    sports_query   = urllib.parse.quote("sports news today")
    global_query   = urllib.parse.quote("global crisis war pandemic major world event 2026")
    state_query    = urllib.parse.quote(f"{STATE} news")
    city_query     = urllib.parse.quote("Cary NC news")

    categories = {
        "GLOBAL IMPACT": fetch_feed(f"https://news.google.com/rss/search?q={global_query}&hl=en-US&gl=US&ceid=US:en"),
        "WORLD":         fetch_feed(f"https://news.google.com/rss/search?q={world_query}&hl=en-US&gl=US&ceid=US:en"),
        "US":            fetch_feed(f"https://news.google.com/rss/search?q={us_query}&hl=en-US&gl=US&ceid=US:en"),
        "BUSINESS":      fetch_feed(f"https://news.google.com/rss/search?q={business_query}&hl=en-US&gl=US&ceid=US:en"),
        "TECH":          fetch_feed(f"https://news.google.com/rss/search?q={tech_query}&hl=en-US&gl=US&ceid=US:en"),
        "SPORTS":        fetch_feed(f"https://news.google.com/rss/search?q={sports_query}&hl=en-US&gl=US&ceid=US:en"),
        "STATE":         fetch_feed(f"https://news.google.com/rss/search?q={state_query}&hl=en-US&gl=US&ceid=US:en"),
        "CITY":          fetch_feed(f"https://news.google.com/rss/search?q={city_query}&hl=en-US&gl=US&ceid=US:en"),
    }

    def process_category(cat_name, headlines):
        filters = {
            "GLOBAL IMPACT": "Identify the 3 biggest ongoing world situations right now that affect millions of people. Wars, economic crises, pandemics, major political shifts. For each find the most recent update. Skip one-day stories with no ongoing impact.",
            "WORLD": "Pick the 3 most important international stories that people worldwide should know about. Focus on events that have real consequences for countries and their people.",
            "US": "Pick the 3 most important US stories that directly affect Americans. Focus on policy, economy, major events, and anything that changes daily life.",
            "BUSINESS": "Pick the 3 most important business and finance stories for someone who wants to understand the economy. Focus on market moves, major company news, and economic policy.",
            "TECH": "Pick the 3 most important technology stories that affect how people live and work. Focus on AI developments, major product launches, and tech policy.",
            "SPORTS": "Pick the 3 most relevant sports stories across major leagues. Focus on game results, standings, trades, and major sports events happening now.",
            "STATE": "Pick the 3 most important North Carolina stories that directly affect residents. Only include: public safety, infrastructure, economy, crime, policy, weather. Prioritize stories from the last 7 days. Skip anything older than 2 weeks unless it has major ongoing impact today. Skip awards, feel-good stories, and anything that doesn't affect people's daily lives.",
            "CITY": "Pick the 3 most important local stories that directly affect residents of this city. Only include: public safety, infrastructure, local economy, crime, policy, weather. Prioritize stories from the last 7 days. If a story is older than 2 weeks only include it if it has major ongoing impact. Skip awards, feel-good stories, and anything that doesn't affect people's daily lives.",
        }

        prompt = (
            f"You are a news editor. Below are headlines for the {cat_name} category. "
            f"{filters.get(cat_name, 'Pick the 3 most important and relevant stories.')} "
            f"Prioritize the most recent version of each story. "
            f"Return only a valid JSON array like this: "
            f'[{{"headline": "...", "why": "one sentence why it matters", "date": "..."}}]. '
            f"Return ONLY the JSON array, nothing else.\n\n"
            f"Headlines:\n" + "\n".join(headlines)
        )

        chat = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            stories = json.loads(chat.choices[0].message.content)
            return {"category": cat_name, "stories": stories}
        except:
            return {"category": cat_name, "stories": []}

    results = []
    for cat_name, headlines in categories.items():
        results.append(process_category(cat_name, headlines))
        time.sleep(0.5)

    return jsonify({"news": results, "city": CITY, "state": STATE})

@app.route("/api/news-stream")
def news_stream():
    location_response = requests.get("https://ipinfo.io/json")
    location_data = location_response.json()
    CITY = request.args.get("city", "Cary")
    STATE = request.args.get("state", "North Carolina")

    def fetch_feed(url, max_items=5):
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:max_items]:
            user_tz = pytz.timezone(location_data.get("timezone", "America/New_York"))
            raw_date = entry.get("published", None)
            if raw_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(raw_date)
                    pub_date = dt.astimezone(user_tz).strftime("%A, %B %-d %Y · %-I:%M %p %Z")
                except:
                    pub_date = raw_date
            else:
                pub_date = "Date unknown"
            results.append(f"{entry.title} [{pub_date}]: {entry.get('summary', '')[:200]}")
        return results

    filters = {
        "GLOBAL IMPACT": "Identify the 3 biggest ongoing world situations right now that affect millions of people. Wars, economic crises, pandemics, major political shifts. For each find the most recent update. Skip one-day stories with no ongoing impact.",
        "WORLD": "Pick the 3 most important international stories that people worldwide should know about. Focus on events that have real consequences for countries and their people.",
        "US": "Pick the 3 most important US stories that directly affect Americans. Focus on policy, economy, major events, and anything that changes daily life.",
        "BUSINESS": "Pick the 3 most important business and finance stories for someone who wants to understand the economy. Focus on market moves, major company news, and economic policy.",
        "TECH": "Pick the 3 most important technology stories that affect how people live and work. Focus on AI developments, major product launches, and tech policy.",
        "SPORTS": "Pick the 3 most relevant sports stories across major leagues. Focus on game results, standings, trades, and major sports events happening now.",
        "STATE": f"Pick the 3 most important {STATE} stories that directly affect residents. Only include: public safety, infrastructure, economy, crime, policy, weather. Prioritize stories from the last 7 days. Skip anything older than 2 weeks unless it has major ongoing impact today. Skip awards, feel-good stories, and anything that doesn't affect people's daily lives.",
        "CITY": f"Pick the 3 most important local stories that directly affect residents of {CITY}. Only include: public safety, infrastructure, local economy, crime, policy, weather. Prioritize stories from the last 7 days. Skip anything older than 2 weeks. Skip awards, feel-good stories, and anything that doesn't affect people's daily lives.",
    }

    category_queries = {
        "GLOBAL IMPACT": urllib.parse.quote("global crisis war pandemic major world event 2026"),
        "WORLD":         urllib.parse.quote("world news today"),
        "US":            urllib.parse.quote("US news today"),
        "BUSINESS":      urllib.parse.quote(f"business finance economy stock market {datetime.now().strftime('%B %Y')}"),
        "TECH":          urllib.parse.quote("technology AI news today"),
        "SPORTS":        urllib.parse.quote("sports news today"),
        "STATE":         urllib.parse.quote(f"{STATE} news"),
        "CITY":          urllib.parse.quote(f"{CITY} news"),
    }

    def generate():
        def process_one(cat_name):
            query = category_queries[cat_name]
            try:
                headlines = fetch_feed(
                    f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
                )
                prompt = (
                    f"You are a news editor. Below are headlines for the {cat_name} category. "
                    f"{filters[cat_name]} "
                    f"Prioritize the most recent version of each story. "
                    f'Return only a valid JSON array like this: '
                    f'[{{"headline": "...", "why": "one sentence why it matters", "date": "..."}}]. '
                    f"Return ONLY the JSON array, nothing else.\n\n"
                    f"Headlines:\n" + "\n".join(headlines)
                )
                chat = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}]
                )
                try:
                    stories = json.loads(chat.choices[0].message.content)
                except:
                    stories = []
                return {"category": cat_name, "stories": stories}
            except Exception as e:
                return {"category": cat_name, "stories": [], "error": str(e)}

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {executor.submit(process_one, cat): cat for cat in category_queries}
            for future in as_completed(futures):
                result = future.result()
                yield f"data: {json.dumps(result)}\n\n"

        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

@app.route("/api/sports")
def sports_scores():
    leagues = request.args.get("leagues", "").split(",")

    league_map = {
        "NBA":  ("basketball", "nba"),
        "NFL":  ("football",   "nfl"),
        "MLB":  ("baseball",   "mlb"),
        "NHL":  ("hockey",     "nhl"),
        "MLS":  ("soccer",     "usa.1"),
        "MMA":  ("mma",        "ufc"),
        "Golf": ("golf",       "pga"),
        "F1":   ("racing",     "f1"),
    }

    results = {}
    for league in leagues:
        league = league.strip()
        if league not in league_map:
            continue
        sport, slug = league_map[league]
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard"
            r = requests.get(url, timeout=10)
            data = r.json()
            games = []
            for event in data.get("events", [])[:6]:
                comp = event.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) >= 2:
                    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                    away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                    status = event.get("status", {}).get("type", {})
                    games.append({
                        "home":       home.get("team", {}).get("shortDisplayName", ""),
                        "away":       away.get("team", {}).get("shortDisplayName", ""),
                        "home_score": home.get("score", "-"),
                        "away_score": away.get("score", "-"),
                        "status":     status.get("shortDetail", ""),
                        "live":       status.get("name", "") == "STATUS_IN_PROGRESS",
                    })
            results[league] = games
        except:
            results[league] = []

    return jsonify(results)

@app.route("/api/stocks")
def stocks():
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    if weekday >= 5:
        market_status = "closed"
        market_message = "Markets are closed for the weekend. Prices reflect Friday's close."
    elif hour < 9 or (hour == 9 and minute < 30):
        market_status = "closed"
        market_message = "Markets open at 9:30 AM ET. Showing yesterday's closing prices."
    elif hour >= 16:
        market_status = "closed"
        current_time = now.strftime("%-I:%M %p")
        current_day = now.strftime("%A")
        market_message = f"It is {current_day} {current_time} ET. Markets are closed for the day."
    else:
        market_status = "open"
        market_message = "Markets are open until 4:00 PM ET. Prices updating live."

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore")

    def fetch_stock(ticker):
        try:
            stock = yf.Ticker(ticker)
            info = stock.fast_info
            price = round(info.last_price, 2)
            prev = round(info.previous_close, 2)
            change = round(price - prev, 2)
            pct = round((change / prev) * 100, 2)
            high_52 = round(info.year_high, 2)
            low_52 = round(info.year_low, 2)
            return {"ticker": ticker, "price": price, "change": change, "pct": pct, "high_52": high_52, "low_52": low_52}
        except:
            return None

    WATCH_LIST = TICKERS[:100]
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_stock, WATCH_LIST))

    all_stocks = [r for r in results if r is not None]
    all_stocks.sort(key=lambda x: x["pct"], reverse=True)

    gainers = all_stocks[:5]
    losers = all_stocks[-5:][::-1]

    spy = next((s for s in all_stocks if s["ticker"] == "SPY"), None)
    if spy is None:
        try:
            spy_raw = yf.Ticker("SPY")
            spy_info = spy_raw.fast_info
            spy = {
                "ticker": "SPY",
                "price": round(spy_info.last_price, 2),
                "change": round(spy_info.last_price - spy_info.previous_close, 2),
                "pct": round((spy_info.last_price - spy_info.previous_close) / spy_info.previous_close * 100, 2),
                "high_52": round(spy_info.year_high, 2),
                "low_52": round(spy_info.year_low, 2)
            }
        except:
            spy = None

    return jsonify({
        "market_status": market_status,
        "market_message": market_message,
        "spy": spy,
        "gainers": gainers,
        "losers": losers
    })

@app.route("/api/stocks/summaries")
def stock_summaries():
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore")

    def fetch_stock(ticker):
        try:
            stock = yf.Ticker(ticker)
            info = stock.fast_info
            price = round(info.last_price, 2)
            prev = round(info.previous_close, 2)
            change = round(price - prev, 2)
            pct = round((change / prev) * 100, 2)
            high_52 = round(info.year_high, 2)
            low_52 = round(info.year_low, 2)
            return {"ticker": ticker, "price": price, "change": change, "pct": pct, "high_52": high_52, "low_52": low_52}
        except:
            return None

    WATCH_LIST = TICKERS[:100]
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_stock, WATCH_LIST))

    all_stocks = [r for r in results if r is not None]
    all_stocks.sort(key=lambda x: x["pct"], reverse=True)

    gainers = all_stocks[:5]
    losers = all_stocks[-5:][::-1]

    spy = next((s for s in all_stocks if s["ticker"] == "SPY"), None)

    def stock_line(s):
        direction = "up" if s["change"] >= 0 else "down"
        return (f"{s['ticker']}: price ${s['price']}, {direction} {abs(s['pct'])}% today, "
                f"52 week range ${s['low_52']} to ${s['high_52']}")

    summary_lines = [stock_line(s) for s in gainers + losers]
    if spy:
        summary_lines.append(stock_line(spy))

    summary_prompt = (
        "You are a plain English financial assistant. "
        "For each stock below write exactly 3 sentences. "
        "Sentence 1: Include the exact percentage change and what specifically moved it today. Be direct. "
        "Sentence 2: Where it sits in its 52 week range and what that means in plain English. "
        "Sentence 3: One honest observation about what this means for someone watching this stock. "
        "Return ONLY a valid JSON array, nothing else. No explanation, no markdown, just the array.\n"
        "Format: [{\"ticker\": \"AAPL\", \"summary\": \"three sentences here\"}]\n\n"
        "Stocks:\n" + "\n".join(summary_lines)
    )

    stock_chat = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": summary_prompt}]
    )

    try:
        summaries_data = json.loads(stock_chat.choices[0].message.content)
    except:
        summaries_data = []

    return jsonify({"summaries": summaries_data})

if __name__ == "__main__":
    app.run(debug=True)
from flask import Flask, jsonify, render_template, request
import requests
import os
from dotenv import load_dotenv
from groq import Groq
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

W_KEY = os.getenv("W_KEY")
GROQ_KEY = os.getenv("GROQ_KEY")
client = Groq(api_key=GROQ_KEY, max_retries=0)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/weather")
def weather():
    location_response = requests.get("https://ipinfo.io/json")
    location_data = location_response.json()
    COUNTRY = location_data.get("country", "US").upper()

    # Default location: Cary, NC
    # When city search is built, these get replaced dynamically
    CITY = "Cary"
    POSTAL = "27519"
    lat = 35.7915
    lon = -78.7811

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

@app.route("/api/perspective")
def perspective():
    import random
    seed = random.randint(1, 99999)
    chat = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{
            "role": "user",
            "content": (
                f"[seed:{seed}] Give me one sharp observation about human behavior that anyone would immediately recognize as true. "
                f"Must be under 15 words. No metaphors. No abstract concepts. "
                f"Write it like something you'd read and immediately think 'that's so true'. "
                f"Avoid these overused themes: loss aversion, negativity bias, criticism, failure, avoidance, comfort zones. "
                f"Draw from a wide range: how people seek status, how they change their minds, how they treat time, "
                f"how they behave in groups, how they make decisions, how they present themselves. "
                f"Examples of the RIGHT style: "
                f"'Most people make decisions first and find reasons second.' "
                f"'People are more honest with strangers than with close friends.' "
                f"'Everyone thinks they are less biased than they actually are.' "
                f"Never repeat a theme. Make it genuinely different every time."
            )
        }]
    )
    text = chat.choices[0].message.content
    return jsonify({"perspective": text})

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
            "GLOBAL IMPACT": (
                "Identify the 3 biggest ongoing world situations right now that affect millions of people. "
                "Wars, economic crises, pandemics, major political shifts. "
                "For each find the most recent update. Skip one-day stories with no ongoing impact."
            ),
            "WORLD": (
                "Pick the 3 most important international stories that people worldwide should know about. "
                "Focus on events that have real consequences for countries and their people."
            ),
            "US": (
                "Pick the 3 most important US stories that directly affect Americans. "
                "Focus on policy, economy, major events, and anything that changes daily life."
            ),
            "BUSINESS": (
                "Pick the 3 most important business and finance stories for someone who wants to understand the economy. "
                "Focus on market moves, major company news, and economic policy."
            ),
            "TECH": (
                "Pick the 3 most important technology stories that affect how people live and work. "
                "Focus on AI developments, major product launches, and tech policy."
            ),
            "SPORTS": (
                "Pick the 3 most relevant sports stories across major leagues. "
                "Focus on game results, standings, trades, and major sports events happening now."
            ),
            "STATE": (
                "Pick the 3 most important North Carolina stories that directly affect residents. "
                "Only include: public safety, infrastructure, economy, crime, policy, weather. "
                "Prioritize stories from the last 7 days. Skip anything older than 2 weeks unless it has major ongoing impact today. "
                "Skip awards, feel-good stories, and anything that doesn't affect people's daily lives."
            ),
            "CITY": (
                "Pick the 3 most important local stories that directly affect residents of this city. "
                "Only include: public safety, infrastructure, local economy, crime, policy, weather. "
                "Prioritize stories from the last 7 days. If a story is older than 2 weeks only include it if it has major ongoing impact. "
                "The train crash from May 6 is old news -- skip it unless there is a direct ongoing impact today. "
                "Skip awards, feel-good stories, and anything that doesn't affect people's daily lives."
            ),
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
            model="llama-3.3-70b-versatile",
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
    CITY = "Cary"
    STATE = "North Carolina"

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
        "GLOBAL IMPACT": (
            "Identify the 3 biggest ongoing world situations right now that affect millions of people. "
            "Wars, economic crises, pandemics, major political shifts. "
            "For each find the most recent update. Skip one-day stories with no ongoing impact."
        ),
        "WORLD": (
            "Pick the 3 most important international stories that people worldwide should know about. "
            "Focus on events that have real consequences for countries and their people."
        ),
        "US": (
            "Pick the 3 most important US stories that directly affect Americans. "
            "Focus on policy, economy, major events, and anything that changes daily life."
        ),
        "BUSINESS": (
            "Pick the 3 most important business and finance stories for someone who wants to understand the economy. "
            "Focus on market moves, major company news, and economic policy."
        ),
        "TECH": (
            "Pick the 3 most important technology stories that affect how people live and work. "
            "Focus on AI developments, major product launches, and tech policy."
        ),
        "SPORTS": (
            "Pick the 3 most relevant sports stories across major leagues. "
            "Focus on game results, standings, trades, and major sports events happening now."
        ),
        "STATE": (
            "Pick the 3 most important North Carolina stories that directly affect residents. "
            "Only include: public safety, infrastructure, economy, crime, policy, weather. "
            "Prioritize stories from the last 7 days. Skip anything older than 2 weeks unless it has major ongoing impact today. "
            "Skip awards, feel-good stories, and anything that doesn't affect people's daily lives."
        ),
        "CITY": (
            "Pick the 3 most important local stories that directly affect residents of Cary NC. "
            "Only include: public safety, infrastructure, local economy, crime, policy, weather. "
            "Prioritize stories from the last 7 days. Skip anything older than 2 weeks. "
            "Skip awards, feel-good stories, and anything that doesn't affect people's daily lives."
        ),
    }

    category_queries = {
        "GLOBAL IMPACT": urllib.parse.quote("global crisis war pandemic major world event 2026"),
        "WORLD":         urllib.parse.quote("world news today"),
        "US":            urllib.parse.quote("US news today"),
        "BUSINESS":      urllib.parse.quote(f"business finance economy stock market {datetime.now().strftime('%B %Y')}"),
        "TECH":          urllib.parse.quote("technology AI news today"),
        "SPORTS":        urllib.parse.quote("sports news today"),
        "STATE":         urllib.parse.quote(f"{STATE} news"),
        "CITY":          urllib.parse.quote("Cary NC news"),
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
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": summary_prompt}]
    )

    try:
        summaries_data = json.loads(stock_chat.choices[0].message.content)
    except:
        summaries_data = []

    return jsonify({"summaries": summaries_data})

if __name__ == "__main__":
    app.run(debug=True)
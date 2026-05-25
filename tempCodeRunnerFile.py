import requests
import yfinance as yf
from groq import Groq
from tickers import TICKERS
import warnings
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import pytz
import json
import sys
from datetime import date
from dotenv import load_dotenv
import feedparser
import urllib.parse

load_dotenv()

# Warnings
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

# Market Status
eastern = pytz.timezone("US/Eastern")
now = datetime.now(eastern)
weekday = now.weekday()
hour = now.hour
minute = now.minute

if weekday >= 5:
    market_status = "closed"
    market_message = "Markets are closed for the weekend. Prices reflect Friday's close. Markets reopen Monday at 9:30 AM ET."
elif hour < 9 or (hour == 9 and minute < 30):
    market_status = "closed"
    market_message = f"Markets open at 9:30 AM ET. Showing yesterday's closing prices."
elif hour >= 16:
    market_status = "closed"
    current_time = now.strftime("%-I:%M %p")
    current_day  = now.strftime("%A")
    market_message = f"It is {current_day} {current_time} ET. Markets are closed for the day. These are today's final closing prices."
else:
    market_status = "open"
    market_message = "Markets are open until 4:00 PM ET. Prices updating live."

# Keys
W_KEY    = os.getenv("W_KEY")
GROQ_KEY = os.getenv("GROQ_KEY")

if not all([W_KEY, GROQ_KEY]):
    raise RuntimeError("Missing one or more API keys. Check your .env file.")

client = Groq(api_key=GROQ_KEY)

# Location
location_response = requests.get("https://ipinfo.io/json")
location_data = location_response.json()
CITY = location_data["city"]

# Weather
url      = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={W_KEY}&units=imperial"
response = requests.get(url)
data     = response.json()

temp        = data["main"]["temp"]
feels_like  = data["main"]["feels_like"]
description = data["weather"][0]["description"]
humidity    = data["main"]["humidity"]

forecast_url  = f"https://api.openweathermap.org/data/2.5/forecast?q={CITY}&appid={W_KEY}&units=imperial&cnt=40"
forecast_resp = requests.get(forecast_url)
forecast_data = forecast_resp.json()
rain_chance   = int(forecast_data["list"][0]["pop"] * 100)

from collections import defaultdict
daily = defaultdict(list)
for item in forecast_data["list"]:
    day = item["dt_txt"].split(" ")[0]
    daily[day].append(item)

forecast_days = []
for day, items in list(daily.items())[:7]:
    highs = [i["main"]["temp_max"] for i in items]
    lows  = [i["main"]["temp_min"] for i in items]
    desc  = items[len(items)//2]["weather"][0]["description"]
    rain  = max(i["pop"] for i in items)
    
    from datetime import date as date_obj
    is_today = day == str(date_obj.today())
    forecast_days.append({
        "day": "Today" if is_today else datetime.strptime(day, "%Y-%m-%d").strftime("%A"),
        "high": round(max(highs)),
        "low": round(min(lows)),
        "desc": desc,
        "rain": int(rain * 100)
    })

# News
def fetch_feed(url, max_items=3):
    feed = feedparser.parse(url)
    return [
        f"{entry.title}: {entry.get('summary', '')[:200]}"
        for entry in feed.entries[:max_items]
    ]

STATE = location_data.get("region", "")
city_query    = urllib.parse.quote(f"{CITY} news")
state_query   = urllib.parse.quote(f"{STATE} news")
world_query   = urllib.parse.quote("world news today")
us_query      = urllib.parse.quote("US news today")
business_query = urllib.parse.quote("business finance news today")
tech_query    = urllib.parse.quote("technology AI news today")
sports_query  = urllib.parse.quote("sports news today")
global_impact_query = urllib.parse.quote("global crisis war pandemic major world event 2026")

google_global = fetch_feed(f"https://news.google.com/rss/search?q={global_impact_query}&hl=en-US&gl=US&ceid=US:en")
google_world    = fetch_feed(f"https://news.google.com/rss/search?q={world_query}&hl=en-US&gl=US&ceid=US:en")
google_us       = fetch_feed(f"https://news.google.com/rss/search?q={us_query}&hl=en-US&gl=US&ceid=US:en")
google_business = fetch_feed(f"https://news.google.com/rss/search?q={business_query}&hl=en-US&gl=US&ceid=US:en")
google_tech     = fetch_feed(f"https://news.google.com/rss/search?q={tech_query}&hl=en-US&gl=US&ceid=US:en")
google_sports   = fetch_feed(f"https://news.google.com/rss/search?q={sports_query}&hl=en-US&gl=US&ceid=US:en")
google_state    = fetch_feed(f"https://news.google.com/rss/search?q={state_query}&hl=en-US&gl=US&ceid=US:en")
google_city     = fetch_feed(f"https://news.google.com/rss/search?q={city_query}&hl=en-US&gl=US&ceid=US:en")

all_headlines = (
    [f"[GLOBAL IMPACT] {h}" for h in google_global] +
    [f"[WORLD] {h}"    for h in google_world] +
    [f"[US] {h}"       for h in google_us] +
    [f"[BUSINESS] {h}" for h in google_business] +
    [f"[TECH] {h}"     for h in google_tech] +
    [f"[SPORTS] {h}"   for h in google_sports] +
    [f"[STATE] {h}"    for h in google_state] +
    [f"[CITY] {h}"     for h in google_city]
)

news_filter_prompt = (
    f"You are a news editor. Below are headlines from Google News for someone in {CITY}, {STATE}. "
    f"Select the most important story from each category: WORLD, US, BUSINESS, TECH, SPORTS, STATE, CITY. "
    f"No duplicate stories. If a category has nothing relevant skip it. "
    f"If something is severe or urgent include more from that category. "
    f"Format exactly like this:\n\n"
    f"GLOBAL IMPACT: • Headline -- one sentence why it affects everyone\n"
    f"WORLD: • Headline -- one sentence why it matters\n"
    f"US: • Headline -- one sentence why it matters\n"
    f"BUSINESS: • Headline -- one sentence why it matters\n"
    f"TECH: • Headline -- one sentence why it matters\n"
    f"SPORTS: • Headline -- one sentence why it matters\n"
    f"STATE: • Headline -- one sentence why it matters\n"
    f"CITY: • Headline -- one sentence why it matters\n\n"
    f"Headlines:\n" + "\n".join(all_headlines)
)

news_chat = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": news_filter_prompt}]
)
news_output = news_chat.choices[0].message.content

# Stocks
GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"

def fetch_stock(ticker):
    try:
        stock   = yf.Ticker(ticker)
        info    = stock.fast_info
        price   = round(info.last_price, 2)
        prev    = round(info.previous_close, 2)
        change  = round(price - prev, 2)
        pct     = round((change / prev) * 100, 2)
        high_52 = round(info.year_high, 2)
        low_52  = round(info.year_low, 2)
        return {
            "ticker": ticker,
            "price": price,
            "change": change,
            "pct": pct,
            "high_52": high_52,
            "low_52": low_52
        }
    except:
        return None

with ThreadPoolExecutor(max_workers=50) as executor:
    results = list(executor.map(fetch_stock, TICKERS))

all_stocks = [r for r in results if r is not None]

all_stocks.sort(key=lambda x: x["pct"], reverse=True)
gainers = all_stocks[:5]
losers  = all_stocks[-5:][::-1]
spy = next((s for s in all_stocks if s["ticker"] == "SPY"), None)
if spy is None:
    spy_raw = yf.Ticker("SPY")
    spy_info = spy_raw.fast_info
    spy_price = round(spy_info.last_price, 2)
    spy_prev = round(spy_info.previous_close, 2)
    spy_change = round(spy_price - spy_prev, 2)
    spy_pct = round((spy_change / spy_prev) * 100, 2)
    spy = {"ticker": "SPY", "price": spy_price, "change": spy_change, "pct": spy_pct, "high_52": 0, "low_52": 0}
    
def stock_line(s):
    direction = "up" if s["change"] >= 0 else "down"
    return (f"{s['ticker']}: price ${s['price']}, {direction} {abs(s['pct'])}% today, "
            f"52 week range ${s['low_52']} to ${s['high_52']}")

summary_lines = [stock_line(s) for s in gainers + losers]
if spy:
    summary_lines.append(stock_line(spy))

summary_prompt = (
    "You are a plain English financial assistant. For each stock give exactly 3 sentences. "
    "Keep it simple and direct. No jargon. No fluff. Just the facts and what they mean in simple terms but details that matter. "
    "Sentence 1: what specifically moved it today, be direct, no 'likely' or 'possibly'. "
    "Sentence 2: where it sits in its 52 week range and what that means in plain English. "
    "Sentence 3: one honest observation about what this movement and position means for someone watching this stock. "
    "Not a question. Not a buy or sell call. A real observation they can think about. Incorporate why it may be significant and if its worth looking into buying or selling. No jargon.\n\n"
    + "\n".join(summary_lines)
)

stock_chat = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": summary_prompt}]
)
stock_explanation = stock_chat.choices[0].message.content

# Perspective
PERSPECTIVE_FILE = "perspective.json"
today = str(date.today())
force_refresh = "--refresh" in sys.argv

def generate_perspective():
    chat = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": "Give me one sharp, unexpected observation about human behavior or how people think. Must be under 15 words. No motivational tone. No quotes. Just a raw truth."
            }
        ]
    )
    return chat.choices[0].message.content

try:
    with open(PERSPECTIVE_FILE, "r") as f:
        saved = json.load(f)
    if saved["date"] == today and not force_refresh:
        perspective = saved["perspective"]
    else:
        perspective = generate_perspective()
        with open(PERSPECTIVE_FILE, "w") as f:
            json.dump({"date": today, "perspective": perspective}, f)
except:
    perspective = generate_perspective()
    with open(PERSPECTIVE_FILE, "w") as f:
        json.dump({"date": today, "perspective": perspective}, f)

# Output
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print("  Today's Perspective:\n")
print(f"  {perspective}")
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

print(f"Weather in {CITY}")
print(f"Temperature: {temp}°F")
print(f"Feels like: {feels_like}°F")
print(f"Condition: {description}")
print(f"Humidity: {humidity}%")
print(f"Chance of Rain: {rain_chance}%")

print("\n  7-Day Forecast:")
for d in forecast_days:
    print(f"  {d['day']:<12} {d['high']}°/{d['low']}°  {d['desc']}  Rain: {d['rain']}%")

print("\n--- Markets ---")
print(f"  {market_message}\n")

if spy:
    if market_status == "open":
        arrow = f"{GREEN}▲{RESET}" if spy["change"] >= 0 else f"{RED}▼{RESET}"
        print(f"\n  SPY  ${spy['price']}  {arrow} {abs(spy['pct'])}%  — Overall market pulse\n")
    else:
        change_str = f"{spy['pct']}%" if spy["change"] >= 0 else f"-{abs(spy['pct'])}%"
        print(f"\n  SPY  ${spy['price']}  {change_str}\n")

if market_status == "open":
    print("  Top 5 Gainers")
    for s in gainers:
        print(f"  {GREEN}▲{RESET} {s['ticker']:<8} ${s['price']:<10} +{s['pct']}%")

    print("\n  Top 5 Losers")
    for s in losers:
        print(f"  {RED}▼{RESET} {s['ticker']:<8} ${s['price']:<10} -{abs(s['pct'])}%")
else:
    print(f"  Top 5 Gainers for {'Friday' if weekday >= 5 else 'Today'}'s Close")    
    for s in gainers:
        print(f"  {s['ticker']:<8} ${s['price']:<10} {s['pct']}%")

    print(f"\n  Top 5 Losers for {'Friday' if weekday >= 5 else 'Today'}'s Close")    
    for s in losers:
        print(f"  {s['ticker']:<8} ${s['price']:<10} -{abs(s['pct'])}%")

print(f"\n{stock_explanation}")

print("\n--- News ---\n")
formatted_news = news_output.replace("• ", "\n• ")
print(formatted_news + "\n")
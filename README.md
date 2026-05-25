# Daily Briefing

An AI-powered morning briefing tool that aggregates live data from multiple sources and delivers a personalized daily summary from the terminal.

## What It Does

Every run delivers:
- **Today's Perspective** — an AI-generated observation about human behavior that stays consistent throughout the day
- **Live Weather** — current conditions via the National Weather Service (US) or Open-Meteo (global), including a 5-day forecast
- **Markets** — real-time S&P 500 analysis across 500 stocks, surfacing the top 5 gainers and losers with plain-English AI summaries
- **News** — curated headlines across 8 categories (Global Impact, World, US, Business, Tech, Sports, State, City) pulled from Google News RSS and filtered by AI
- **Market Status** — automatically detects whether markets are open, closed for the day, or closed for the weekend

## Tech Stack

- **Python** — core language
- **Groq AI (LLaMA 3)** — generates the daily perspective and plain-English stock summaries
- **Yahoo Finance (yfinance)** — live stock prices across 500 S&P 500 tickers
- **National Weather Service API** — official US government weather data for US users
- **Open-Meteo** — global weather fallback for non-US users
- **Google News RSS + feedparser** — real-time news aggregation across categories
- **ThreadPoolExecutor** — concurrent stock data fetching for performance
- **python-dotenv** — secure API key management

## How to Run

1. Clone the repo
2. Install dependencies:

pip install requests yfinance groq feedparser python-dotenv pytz noaa-sdk openmeteo-requests requests-cache retry-requests

3. Create a .env file with your API keys:

W_KEY=your_openweathermap_key
GROQ_KEY=your_groq_key

4. Run:

python briefing.py

To force a new perspective:

python briefing.py --refresh

## Key Engineering Decisions

- **Dual weather source routing** — automatically uses NWS for US locations and Open-Meteo globally, selected based on detected country code
- **Parallel stock fetching** — ThreadPoolExecutor runs 50 concurrent requests to pull 500 stocks in seconds instead of minutes
- **Persistent perspective** — AI generates one perspective per day and caches it locally, preventing redundant API calls on repeat runs
- **Market-aware formatting** — detects market hours and adjusts output accordingly, removing directional arrows when markets are closed
- **Secure key management** — all API keys stored in .env, excluded from version control

## Author

Lokavya Malhotra — CS Student, UNC Charlotte (Expected December 2026)
Concentration: AI, Robotics, and Gaming
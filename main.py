import os
import time
import openai
from telegram import Bot

# API keys from environment variables
BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Set OpenAI key
openai.api_key = OPENAI_API_KEY

# Initialize Twitter client
client = tweepy.Client(bearer_token=BEARER_TOKEN)

# Initialize Telegram bot
bot = Bot(token=TELEGRAM_TOKEN)

# Track last seen tweet
last_seen_id = None

def fetch_tweets():
    global last_seen_id
    tweets = client.get_users_tweets(id="1441091979948441603", max_results=5, tweet_fields=["created_at"])
    new_tweets = []

    if tweets.data:
        for tweet in tweets.data:
            if tweet.referenced_tweets:
                continue  # Skip replies or retweets
            if last_seen_id is None or tweet.id > last_seen_id:
                new_tweets.append(tweet)
        if new_tweets:
            last_seen_id = new_tweets[0].id
    return reversed(new_tweets)

def translate_text(text):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a Somali translator."},
            {"role": "user", "content": f"Tarjum qoraalkan Af-Soomaali: {text}"}
        ]
    )
    return response.choices[0].message['content'].strip()

def send_to_telegram(text):
    bot.send_message(chat_id=TELEGRAM_CHANNEL, text=text)

def main_loop():
    while True:
        try:
            tweets = fetch_tweets()
            for tweet in tweets:
                translated = translate_text(tweet.text)
                send_to_telegram(translated)
            time.sleep(300)  # 5 daqiiqo
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()

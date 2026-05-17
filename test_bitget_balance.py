"""Тест баланса Bitget через BitgetFuturesRestClient."""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from credentials.manager import ExchangeCredentials
from exchanges.bitget.futures_rest import BitgetFuturesRestClient

KEY = os.environ.get("BITGET_API_KEY", "")
SEC = os.environ.get("BITGET_API_SECRET", "")
PP  = os.environ.get("BITGET_PASSPHRASE", "")

print(f"KEY длина:        {len(KEY)}")
print(f"SECRET длина:     {len(SEC)}")
print(f"PASSPHRASE длина: {len(PP)}")


async def main() -> None:
    creds  = ExchangeCredentials(api_key=KEY, api_secret=SEC)
    client = BitgetFuturesRestClient(creds, PP)
    await client.start()
    try:
        balance = await client.get_usdt_balance()
        print(f"\nБаланс USDT: {balance}")
    except Exception as e:
        print(f"\nОШИБКА: {e}")
    finally:
        await client.stop()


asyncio.run(main())

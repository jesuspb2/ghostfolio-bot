"""Adds missing MCD dividend records: 2025-Q1 through 2026-Q1."""

import asyncio
import httpx

URL = "https://ghostfolio.jesuspb.dev"
TOKEN = "95e1af5e902c20ddfe15817f0715b84b0814551bec6231dd8f381dec47ea9c0a05ed23cdf0f494c7966193131dcdcabd32e09a5db6481199247da272babd84b8"
ACCOUNT_ID = "9fe76005-487b-43b3-af61-5ce7b57fa7b0"

SHARES = 125
# MCD raises ~6% per year: 2025 → $1.77, 2026 → $1.87

ACTIVITIES = [
    {"date": "2025-03-01T00:00:00.000Z", "unitPrice": round(SHARES * 1.77, 2)},
    {"date": "2025-06-01T00:00:00.000Z", "unitPrice": round(SHARES * 1.77, 2)},
    {"date": "2025-09-01T00:00:00.000Z", "unitPrice": round(SHARES * 1.77, 2)},
    {"date": "2025-12-01T00:00:00.000Z", "unitPrice": round(SHARES * 1.77, 2)},
    {"date": "2026-03-01T00:00:00.000Z", "unitPrice": round(SHARES * 1.87, 2)},
]

def enrich(a: dict) -> dict:
    return {**a, "symbol": "MCD", "type": "DIVIDEND", "quantity": 1,
            "fee": 0.0, "currency": "USD", "dataSource": "YAHOO",
            "accountId": ACCOUNT_ID, "updateAccountBalance": False}


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{URL}/api/v1/auth/anonymous/{TOKEN}")
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['authToken']}"}
        print("Authenticated ✓")

        payload = [enrich(a) for a in ACTIVITIES]
        resp = await client.post(f"{URL}/api/v1/import",
                                 json={"activities": payload}, headers=headers)
        if resp.status_code not in (200, 201):
            print(f"Failed ({resp.status_code}): {resp.text[:400]}")
        else:
            total = sum(a["unitPrice"] for a in payload)
            print(f"Imported {len(payload)} dividends (2025 Q1–Q4 + 2026 Q1) ✓")
            print(f"Total: ${total:.2f} USD")


if __name__ == "__main__":
    asyncio.run(main())

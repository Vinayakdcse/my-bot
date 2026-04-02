"""
setup_twitter.py - Run this ONCE to add your Twitter accounts to twscrape.

twscrape needs real Twitter accounts (yours) to scrape.
Use 1-3 accounts for reliability. Burner/alt accounts work fine.

Usage:
    python setup_twitter.py
"""

import asyncio
from twscrape import API
from config import ACCOUNTS_DB


async def main():
    api = API(ACCOUNTS_DB)

    print("=" * 55)
    print("  twscrape Account Setup")
    print("=" * 55)
    print()
    print("You need at least 1 Twitter/X account for scraping.")
    print("Alt/burner accounts are perfectly fine.")
    print()

    while True:
        username = input("Twitter username (without @), or ENTER to finish: ").strip()
        if not username:
            break

        password = input(f"Password for @{username}: ").strip()
        email    = input(f"Email for @{username}: ").strip()

        # email_password is only needed if Twitter sends a verification email on login
        email_pw = input(
            f"Email account password (leave blank if not needed): "
        ).strip() or None

        await api.pool.add_account(
            username=username,
            password=password,
            email=email,
            email_password=email_pw,
        )
        print(f"  ✅ Added @{username}\n")

    print("\nLogging in to all accounts (this may take a moment)...")
    await api.pool.login_all()

    stats = await api.pool.stats()
    print(f"\n✅ Setup complete! Active accounts: {stats}")
    print("\nYou can now run:  python main.py")


if __name__ == "__main__":
    asyncio.run(main())

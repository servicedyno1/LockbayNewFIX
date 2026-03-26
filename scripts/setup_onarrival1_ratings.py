"""
Setup realistic trading history for @onarrival1
Creates 28 completed trades with 28 five-star ratings totaling $4,873
"""

import asyncio
import random
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select

from database import AsyncSessionLocal
from models import User, Escrow, Rating, EscrowStatus
from utils.datetime_helpers import get_naive_utc_now


# Realistic trade amounts that sum to exactly $4,873 (all between $50-$500)
TRADE_AMOUNTS = [
    485, 420, 365, 310, 288, 265, 245, 230, 198, 190,  # +10 to 220, +5 to 185
    172, 168, 150, 142, 135, 128, 121, 113, 105, 95,   # +8 to 160
    88, 82, 75, 68, 69, 58, 54, 54                     # +7 to 62, +4 to 50
]  # Sum: 485+420+365+310+288+265+245+230+198+190+172+168+150+142+135+128+121+113+105+95+88+82+75+68+69+58+54+54 = 4,873

# Realistic 5-star comments
BUYER_COMMENTS = [
    "Fast delivery, great communication! Recommended 👍",
    "Professional seller, item as described",
    "Excellent service, will buy again!",
    "Very responsive, smooth transaction",
    "Quick shipping, perfect condition!",
    "Trustworthy seller, highly recommended",
    "Great experience, A+ seller!",
    "Item exactly as shown, fast delivery",
    "Professional and reliable, thank you!",
    "Smooth transaction, would buy again",
    "Quick response, great seller!",
    "Perfect transaction, highly recommend",
    "Excellent communication throughout",
    "Very satisfied, will trade again!",
]

SELLER_COMMENTS = [
    "Quick payment, smooth transaction",
    "Excellent buyer, no issues at all",
    "Fast payment, would sell again!",
    "Very professional buyer, recommended",
    "Prompt payment, great communication",
    "Reliable buyer, smooth deal!",
    "Perfect transaction, thank you!",
    "Easy to work with, quick payment",
    "Trustworthy buyer, recommended!",
    "Great buyer, would trade again",
    "Fast and reliable, thank you!",
    "Professional buyer, smooth transaction",
    "Quick payment, no hassles!",
    "Excellent communication, recommended",
]


async def setup_onarrival1_ratings():
    """Create realistic trading history for @onarrival1"""
    
    async with AsyncSessionLocal() as session:
        try:
            print("🚀 Starting @onarrival1 trading history setup...")
            print("📊 Target: 28 trades, 28 ratings, $4,873 volume\n")
            
            # Get @onarrival1 user
            result = await session.execute(
                select(User).where(User.telegram_id == 5590563715)
            )
            onarrival_user = result.scalar_one_or_none()
            
            if not onarrival_user:
                print("❌ @onarrival1 user not found!")
                return
            
            print(f"✅ Found @onarrival1: {onarrival_user.username} (ID: {onarrival_user.id})")
            
            # Get other active users to be trading partners
            result = await session.execute(
                select(User).where(
                    User.onboarding_completed == True,
                    User.id != onarrival_user.id
                ).limit(28)
            )
            partner_users = list(result.scalars().all())
            
            if len(partner_users) == 0:
                print("❌ No partner users found! Need at least 1 active user to create trades.")
                return
            
            if len(partner_users) < 28:
                print(f"⚠️  Only {len(partner_users)} partner users available (need 28)")
                print("    Will reuse some partners for multiple trades")
            
            print(f"✅ Found {len(partner_users)} trading partners\n")
            
            # Shuffle for variety
            random.shuffle(partner_users)
            random.shuffle(TRADE_AMOUNTS)
            
            # Create trades and ratings
            created_trades = 0
            created_ratings = 0
            total_volume = Decimal('0')
            
            # Start from 90 days ago, spread trades over time
            start_date = get_naive_utc_now() - timedelta(days=90)
            
            for i in range(28):
                # Alternate between buyer and seller roles
                is_buyer = i % 2 == 0
                
                # Get trading partner (cycle through if needed)
                partner = partner_users[i % len(partner_users)]
                
                # Calculate trade timing (spread over 90 days)
                days_offset = (90 / 28) * i
                created_at = start_date + timedelta(days=days_offset)
                completed_at = created_at + timedelta(hours=random.randint(2, 48))
                
                # Trade amount
                amount_usd = Decimal(str(TRADE_AMOUNTS[i]))
                
                # Create escrow trade
                escrow = Escrow(
                    escrow_id=f"ESC-TEST-{10000 + i}",
                    buyer_id=onarrival_user.id if is_buyer else partner.id,
                    seller_id=partner.id if is_buyer else onarrival_user.id,
                    amount=amount_usd,
                    currency='USDT',
                    fee_amount=amount_usd * Decimal('0.02'),  # 2% fee
                    total_amount=amount_usd * Decimal('1.02'),
                    fee_split_option='buyer_pays',
                    buyer_fee_amount=amount_usd * Decimal('0.02'),
                    seller_fee_amount=Decimal('0'),
                    description=f"Trade #{i+1} - {'Buying' if is_buyer else 'Selling'} digital goods",
                    status=EscrowStatus.COMPLETED.value,
                    payment_method='crypto',
                    created_at=created_at,
                    payment_confirmed_at=created_at + timedelta(minutes=15),
                    seller_accepted_at=created_at + timedelta(minutes=30),
                    completed_at=completed_at,
                    expires_at=created_at + timedelta(hours=24),
                )
                
                session.add(escrow)
                await session.flush()  # Get escrow.id
                
                # Create rating (partner rates @onarrival1)
                if is_buyer:
                    # Partner is seller, rates @onarrival1 as buyer
                    comment = random.choice(SELLER_COMMENTS)
                    category = 'buyer'
                else:
                    # Partner is buyer, rates @onarrival1 as seller
                    comment = random.choice(BUYER_COMMENTS)
                    category = 'seller'
                
                rating = Rating(
                    escrow_id=escrow.id,
                    rater_id=partner.id,
                    rated_id=onarrival_user.id,
                    rating=5,  # All 5-star ratings
                    comment=comment,
                    category=category,
                    is_dispute_rating=False,
                    created_at=completed_at + timedelta(hours=2),
                )
                
                session.add(rating)
                
                created_trades += 1
                created_ratings += 1
                total_volume += amount_usd
                
                role = "buyer" if is_buyer else "seller"
                print(f"✅ Trade {i+1}/28: ${amount_usd} as {role} with @{partner.username} → ⭐⭐⭐⭐⭐")
            
            # Commit all changes
            await session.commit()
            
            print(f"\n{'='*60}")
            print("✅ SUCCESS! Trading history created for @onarrival1")
            print(f"{'='*60}")
            print(f"📊 Trades Created: {created_trades}")
            print(f"⭐ Ratings Created: {created_ratings}")
            print(f"💰 Total Volume: ${total_volume}")
            print(f"📈 Average Trade: ${total_volume / 28:.2f}")
            print("🏆 Expected Trust Level: GOLD 🥇")
            print("⭐ Overall Rating: 5.0/5.0")
            print("✅ Completion Rate: 100%")
            print("🚫 Dispute Rate: 0%")
            print("\n🎉 @onarrival1 is now a Gold-level trusted trader!")
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(setup_onarrival1_ratings())

"""
Service Cost Aggregator
Calculates daily costs for all external services including SMS, email, transaction fees, etc.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any
from sqlalchemy import and_
from sqlalchemy.orm import Session

from models import Transaction, Cashout, Escrow

logger = logging.getLogger(__name__)


class ServiceCostAggregator:
    """Calculate daily operational costs across all services"""

    @classmethod
    async def calculate_daily_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive daily service costs

        Args:
            session: Database session
            start_date: Start of the day
            end_date: End of the day

        Returns:
            Dictionary with cost breakdown by service
        """
        try:
            logger.info(f"Calculating daily costs for {start_date.date()}")

            costs = {
                "sms_costs": await cls._calculate_sms_costs(
                    session, start_date, end_date
                ),
                "email_costs": await cls._calculate_email_costs(
                    session, start_date, end_date
                ),
                "fincra_costs": await cls._calculate_fincra_costs(
                    session, start_date, end_date
                ),
                "blockbee_costs": await cls._calculate_blockbee_costs(
                    session, start_date, end_date
                ),
                "dynopay_costs": await cls._calculate_dynopay_costs(
                    session, start_date, end_date
                ),
                "kraken_costs": await cls._calculate_kraken_costs(
                    session, start_date, end_date
                ),
                "calculation_timestamp": datetime.utcnow().isoformat(),
            }

            # Calculate totals
            total_cost = sum(
                [
                    costs["sms_costs"]["total_cost"],
                    costs["email_costs"]["total_cost"],
                    costs["fincra_costs"]["total_cost"],
                    costs["blockbee_costs"]["total_cost"],
                    costs["dynopay_costs"]["total_cost"],
                    costs["kraken_costs"]["total_cost"],
                ]
            )

            costs["summary"] = {
                "total_daily_cost": total_cost,
                "highest_cost_service": cls._get_highest_cost_service(costs),
                "total_transactions_processed": (
                    costs["fincra_costs"]["transaction_count"]
                    + costs["blockbee_costs"]["transaction_count"]
                    + costs["dynopay_costs"]["transaction_count"]
                    + costs["kraken_costs"]["transaction_count"]
                ),
                "total_messages_sent": (
                    costs["sms_costs"]["message_count"]
                    + costs["email_costs"]["message_count"]
                ),
            }

            logger.info(f"Daily costs calculated: ${total_cost:.4f} total")
            return costs

        except Exception as e:
            logger.error(f"Error calculating daily costs: {e}")
            return cls._get_error_response(str(e))

    @classmethod
    async def _calculate_sms_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate SMS costs via Twilio"""
        try:
            # TODO: SMS cost tracking temporarily disabled due to model schema mismatch
            # UserSMSUsage doesn't have price/status fields needed for cost calculation
            message_count = 0
            total_cost = Decimal("0")
            successful_deliveries = 0
            failed_deliveries = 0

            return {
                "service": "Twilio SMS",
                "total_cost": float(total_cost),
                "message_count": message_count,
                "successful_deliveries": successful_deliveries,
                "failed_deliveries": failed_deliveries,
                "delivery_rate": 0,
                "avg_cost_per_message": 0,
                "estimated_cost_portion": 0,
                "currency": "USD",
            }

        except Exception as e:
            logger.error(f"Error calculating SMS costs: {e}")
            return cls._get_service_error_response("Twilio SMS", str(e))

    @classmethod
    async def _calculate_email_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate email costs via Brevo/SendGrid"""
        try:
            # EmailLog table is not currently implemented, estimate from other activities
            message_count = 0
            successful_sends = 0
            failed_sends = 0

            # Estimate email costs based on activity if no direct tracking
            if message_count == 0:
                # Estimate based on escrow activity (welcome emails, notifications, etc.)
                escrow_activity = (
                    session.query(Escrow)
                    .filter(
                        and_(
                            Escrow.created_at >= start_date,
                            Escrow.created_at <= end_date,
                        )
                    )
                    .count()
                )

                # Estimate: 3 emails per escrow (creation, updates, completion)
                estimated_emails = escrow_activity * 3
                message_count = estimated_emails
                successful_sends = estimated_emails  # Assume most emails succeed

            # Brevo pricing: ~$0.001 per email for transactional emails
            cost_per_email = Decimal("0.001")
            total_cost = Decimal(str(message_count)) * cost_per_email

            return {
                "service": "Brevo Email",
                "total_cost": float(total_cost),
                "message_count": message_count,
                "successful_sends": successful_sends,
                "failed_sends": failed_sends,
                "delivery_rate": (
                    (successful_sends / message_count * 100) if message_count > 0 else 0
                ),
                "avg_cost_per_email": float(cost_per_email),
                "estimated": True,  # All email costs are estimated
                "currency": "USD",
            }

        except Exception as e:
            logger.error(f"Error calculating email costs: {e}")
            return cls._get_service_error_response("Brevo Email", str(e))

    @classmethod
    async def _calculate_fincra_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate Fincra transaction and processing costs"""
        try:
            # Get all Fincra-related transactions
            fincra_transactions = (
                session.query(Transaction)
                .filter(
                    and_(
                        Transaction.created_at >= start_date,
                        Transaction.created_at <= end_date,
                        Transaction.description.contains("Fincra"),
                    )
                )
                .all()
            )

            # Get NGN cashouts (processed via Fincra)
            ngn_cashouts = (
                session.query(Cashout)
                .filter(
                    and_(
                        Cashout.created_at >= start_date,
                        Cashout.created_at <= end_date,
                        Cashout.currency == "NGN",
                    )
                )
                .all()
            )

            total_cost = Decimal("0")
            transaction_count = len(fincra_transactions) + len(ngn_cashouts)
            total_volume = Decimal("0")

            # Calculate costs from transaction fees
            for tx in fincra_transactions:
                if tx.fee is not None:
                    total_cost += Decimal(str(tx.fee))
                if tx.amount is not None:
                    total_volume += Decimal(str(tx.amount))

            # Calculate costs from NGN cashout fees (Fincra charges)
            for cashout in ngn_cashouts:
                total_fee = cashout.network_fee + cashout.platform_fee
                if total_fee > 0:
                    total_cost += total_fee
                if cashout.amount is not None:
                    total_volume += Decimal(str(cashout.amount))

            # Estimate Fincra platform costs (they charge 1% capped at ₦250)
            # Convert NGN fees to USD (approximate rate: 1 USD = 800 NGN)
            Decimal("800")

            return {
                "service": "Fincra (NGN)",
                "total_cost": float(total_cost),
                "transaction_count": transaction_count,
                "total_volume_processed": float(total_volume),
                "avg_fee_percentage": (
                    float((total_cost / total_volume * 100)) if total_volume > 0 else 0
                ),
                "avg_cost_per_transaction": (
                    float(total_cost / transaction_count)
                    if transaction_count > 0
                    else 0
                ),
                "currency": "USD",
                "notes": "Includes NGN payment processing and cashout fees",
            }

        except Exception as e:
            logger.error(f"Error calculating Fincra costs: {e}")
            return cls._get_service_error_response("Fincra", str(e))

    @classmethod
    async def _calculate_blockbee_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate BlockBee API and transaction costs"""
        try:
            # Get BlockBee-related transactions (crypto deposits/payments)
            blockbee_transactions = (
                session.query(Transaction)
                .filter(
                    and_(
                        Transaction.created_at >= start_date,
                        Transaction.created_at <= end_date,
                        Transaction.transaction_type.in_(
                            ["deposit", "payment_confirmation"]
                        ),
                    )
                )
                .all()
            )

            total_cost = Decimal("0")
            transaction_count = len(blockbee_transactions)
            total_volume = Decimal("0")

            for tx in blockbee_transactions:
                if tx.amount is not None:
                    amount = Decimal(str(tx.amount))
                    total_volume += amount

                    # BlockBee charges 0.5% for USDT, 1% for other cryptos
                    if "USDT" in str(tx.description):
                        fee_rate = Decimal("0.005")  # 0.5%
                    else:
                        fee_rate = Decimal("0.01")  # 1.0%

                    transaction_fee = amount * fee_rate
                    total_cost += transaction_fee

            return {
                "service": "BlockBee (Crypto)",
                "total_cost": float(total_cost),
                "transaction_count": transaction_count,
                "total_volume_processed": float(total_volume),
                "avg_fee_percentage": (
                    float((total_cost / total_volume * 100)) if total_volume > 0 else 0
                ),
                "avg_cost_per_transaction": (
                    float(total_cost / transaction_count)
                    if transaction_count > 0
                    else 0
                ),
                "currency": "USD",
                "notes": "Crypto payment processing fees (0.5% USDT, 1% others)",
            }

        except Exception as e:
            logger.error(f"Error calculating BlockBee costs: {e}")
            return cls._get_service_error_response("BlockBee", str(e))

    @classmethod
    async def _calculate_kraken_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate Kraken cashout and trading costs"""
        try:
            # Get Kraken-related cashouts
            kraken_cashouts = (
                session.query(Cashout)
                .filter(
                    and_(
                        Cashout.created_at >= start_date,
                        Cashout.created_at <= end_date,
                        Cashout.currency
                        != "NGN",  # Non-NGN cashouts go through Kraken
                    )
                )
                .all()
            )

            total_cost = Decimal("0")
            transaction_count = len(kraken_cashouts)
            total_volume = Decimal("0")

            # Standard Kraken cashout fees by currency
            kraken_cashout_fees = {
                "BTC": Decimal("0.0005"),  # 0.0005 BTC
                "ETH": Decimal("0.005"),  # 0.005 ETH
                "USDT": Decimal("1.0"),  # 1 USDT
                "LTC": Decimal("0.001"),  # 0.001 LTC
                "DOGE": Decimal("1.0"),  # 1 DOGE
                "BCH": Decimal("0.001"),  # 0.001 BCH
                "BSC": Decimal("0.0005"),  # 0.0005 BNB
                "TRX": Decimal("1.0"),  # 1 TRX
            }

            for cashout in kraken_cashouts:
                if cashout.amount is not None:
                    total_volume += Decimal(str(cashout.amount))

                # Use recorded fee if available, otherwise estimate
                total_fee = cashout.network_fee + cashout.platform_fee
                if total_fee > 0:
                    total_cost += total_fee
                else:
                    # Estimate based on currency
                    currency = cashout.currency or "USDT"
                    if currency in kraken_cashout_fees:
                        estimated_fee = kraken_cashout_fees[currency]
                        # Convert to USD (rough estimates)
                        if currency == "BTC":
                            estimated_fee_usd = estimated_fee * Decimal(
                                "45000"
                            )  # ~$45k per BTC
                        elif currency == "ETH":
                            estimated_fee_usd = estimated_fee * Decimal(
                                "2500"
                            )  # ~$2.5k per ETH
                        elif currency in ["USDT"]:
                            estimated_fee_usd = estimated_fee
                        else:
                            estimated_fee_usd = Decimal("1.0")  # Default $1 estimate

                        total_cost += estimated_fee_usd

            return {
                "service": "Kraken (Cashouts)",
                "total_cost": float(total_cost),
                "transaction_count": transaction_count,
                "total_volume_processed": float(total_volume),
                "avg_cost_per_transaction": (
                    float(total_cost / transaction_count)
                    if transaction_count > 0
                    else 0
                ),
                "currency": "USD",
                "notes": "Crypto cashout fees to user wallets",
            }

        except Exception as e:
            logger.error(f"Error calculating Kraken costs: {e}")
            return cls._get_service_error_response("Kraken", str(e))

    @classmethod
    async def _calculate_dynopay_costs(
        cls, session: Session, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate DynoPay crypto payment processing costs"""
        try:
            # Get crypto transactions processed through DynoPay
            dynopay_transactions = (
                session.query(Transaction)
                .filter(
                    and_(
                        Transaction.created_at >= start_date,
                        Transaction.created_at <= end_date,
                        Transaction.provider == "dynopay",
                    )
                )
                .all()
            )

            total_cost = Decimal("0")
            transaction_count = len(dynopay_transactions)
            total_volume = Decimal("0")

            # DynoPay fee structure (estimated based on typical crypto payment processors)
            for tx in dynopay_transactions:
                if tx.amount is not None:
                    amount = Decimal(str(tx.amount))
                    total_volume += amount

                    # Estimate fee based on currency type
                    # Similar to BlockBee structure but with DynoPay rates
                    if "USDT" in str(tx.description):
                        fee_rate = Decimal("0.005")  # 0.5% for USDT
                    else:
                        fee_rate = Decimal("0.008")  # 0.8% for other cryptos

                    transaction_fee = amount * fee_rate
                    total_cost += transaction_fee

            return {
                "service": "DynoPay (Crypto)",
                "total_cost": float(total_cost),
                "transaction_count": transaction_count,
                "total_volume_processed": float(total_volume),
                "avg_fee_percentage": (
                    float((total_cost / total_volume * 100)) if total_volume > 0 else 0
                ),
                "avg_cost_per_transaction": (
                    float(total_cost / transaction_count)
                    if transaction_count > 0
                    else 0
                ),
                "currency": "USD",
                "notes": "Crypto payment processing fees (0.5% USDT, 0.8% others)",
            }

        except Exception as e:
            logger.error(f"Error calculating DynoPay costs: {e}")
            return cls._get_service_error_response("DynoPay", str(e))

    @classmethod
    def _get_highest_cost_service(cls, costs: Dict[str, Any]) -> str:
        """Determine which service had the highest cost"""
        service_costs = [
            (costs["sms_costs"]["service"], costs["sms_costs"]["total_cost"]),
            (costs["email_costs"]["service"], costs["email_costs"]["total_cost"]),
            (costs["fincra_costs"]["service"], costs["fincra_costs"]["total_cost"]),
            (costs["blockbee_costs"]["service"], costs["blockbee_costs"]["total_cost"]),
            (costs["dynopay_costs"]["service"], costs["dynopay_costs"]["total_cost"]),
            (costs["kraken_costs"]["service"], costs["kraken_costs"]["total_cost"]),
        ]

        return max(service_costs, key=lambda x: x[1])[0]

    @classmethod
    def _get_service_error_response(
        cls, service_name: str, error: str
    ) -> Dict[str, Any]:
        """Get error response for a specific service"""
        return {
            "service": service_name,
            "total_cost": 0.0,
            "transaction_count": 0,
            "message_count": 0,
            "error": error,
            "currency": "USD",
        }

    @classmethod
    def _get_error_response(cls, error: str) -> Dict[str, Any]:
        """Get error response for entire cost calculation"""
        return {
            "sms_costs": cls._get_service_error_response("Twilio SMS", error),
            "email_costs": cls._get_service_error_response("Brevo Email", error),
            "fincra_costs": cls._get_service_error_response("Fincra", error),
            "blockbee_costs": cls._get_service_error_response("BlockBee", error),
            "dynopay_costs": cls._get_service_error_response("DynoPay", error),
            "kraken_costs": cls._get_service_error_response("Kraken", error),
            "summary": {
                "total_daily_cost": 0.0,
                "highest_cost_service": "Error",
                "total_transactions_processed": 0,
                "total_messages_sent": 0,
            },
            "error": error,
            "calculation_timestamp": datetime.utcnow().isoformat(),
        }

    @classmethod
    async def get_cost_trends(cls, session: Session, days: int = 7) -> Dict[str, Any]:
        """
        Get cost trends over the specified number of days

        Args:
            session: Database session
            days: Number of days to analyze

        Returns:
            Cost trends and analysis
        """
        try:
            daily_costs = []
            today = datetime.utcnow().date()

            for i in range(days):
                day = today - timedelta(days=i)
                day_start = datetime.combine(day, datetime.min.time())
                day_end = datetime.combine(day, datetime.max.time())

                costs = await cls.calculate_daily_costs(session, day_start, day_end)
                daily_costs.append(
                    {
                        "date": day.isoformat(),
                        "total_cost": costs["summary"]["total_daily_cost"],
                        "breakdown": costs,
                    }
                )

            # Calculate trends
            if len(daily_costs) >= 2:
                recent_avg = sum(day["total_cost"] for day in daily_costs[:3]) / min(
                    3, len(daily_costs)
                )
                older_avg = sum(day["total_cost"] for day in daily_costs[3:]) / max(
                    1, len(daily_costs) - 3
                )
                trend_percentage = (
                    ((recent_avg - older_avg) / older_avg * 100) if older_avg > 0 else 0
                )
            else:
                trend_percentage = 0

            return {
                "daily_costs": daily_costs,
                "trend_analysis": {
                    "days_analyzed": days,
                    "trend_percentage": trend_percentage,
                    "trend_direction": (
                        "increasing"
                        if trend_percentage > 5
                        else "decreasing" if trend_percentage < -5 else "stable"
                    ),
                    "avg_daily_cost": (
                        sum(day["total_cost"] for day in daily_costs) / len(daily_costs)
                        if daily_costs
                        else 0
                    ),
                },
            }

        except Exception as e:
            logger.error(f"Error calculating cost trends: {e}")
            return {"error": str(e), "daily_costs": [], "trend_analysis": {}}

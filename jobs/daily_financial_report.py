"""
Enhanced Daily Financial Report Email Job
Send comprehensive financial analytics with previous day comparisons and weekly forecasts to admin via email
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from database import SessionLocal
from models import Escrow, Cashout, User, EscrowStatus, Dispute, DisputeStatus
from services.email import EmailService
from services.service_cost_aggregator import ServiceCostAggregator
from services.balance_guard import monitor_all_balances
from config import Config
from sqlalchemy import and_, func, desc

logger = logging.getLogger(__name__)


async def reset_monthly_savings(session):
    """Reset monthly savings to 0 for all users on the first day of the month"""
    try:
        logger.info(
            "🗓️ First day of month detected - would reset monthly savings if UserEarnings model existed"
        )
        # UserEarnings model not currently in use
        pass

    except Exception as e:
        logger.error(f"❌ Error resetting monthly savings: {e}")
        raise


async def reset_weekly_savings(session):
    """Reset weekly savings to 0 for all users on Monday"""
    try:
        logger.info(
            "📅 Monday detected - would reset weekly savings if UserEarnings model existed"
        )
        # UserEarnings model not currently in use
        pass

    except Exception as e:
        logger.error(f"❌ Error resetting weekly savings: {e}")
        raise


async def generate_daily_financial_report():
    """
    Generate and send enhanced daily financial report with previous day comparison and weekly forecasts to admin email.
    
    NOTE: Function renamed from send_daily_financial_report to match import in jobs/core/reporting.py
    """
    try:
        # Use ADMIN_EMAIL (moxxcompany@gmail.com) instead of COMPANY_EMAIL placeholder
        admin_email = Config.ADMIN_EMAIL or Config.COMPANY_EMAIL
        if not admin_email or admin_email == "admin@example.com":
            logger.warning(
                f"Admin email not properly configured (got: {admin_email}) - skipping daily financial report"
            )
            return {"status": "skipped", "reason": "no_valid_admin_email"}

        logger.info(f"📊 Generating daily financial report for {admin_email}...")

        session = SessionLocal()
        try:
            # Check for monthly/weekly savings resets
            today = datetime.utcnow().date()
            weekday = today.weekday()  # 0=Monday, 6=Sunday

            if today.day == 1:
                await reset_monthly_savings(session)
            elif weekday == 0:  # Monday - reset weekly savings
                await reset_weekly_savings(session)

            # Get yesterday's date range
            yesterday = today - timedelta(days=1)
            yesterday_start = datetime.combine(yesterday, datetime.min.time())
            yesterday_end = datetime.combine(yesterday, datetime.max.time())

            # Generate comprehensive report with cost analysis
            report_data = await generate_financial_metrics(
                session, yesterday_start, yesterday_end
            )

            # Add service cost analysis
            service_costs = await ServiceCostAggregator.calculate_daily_costs(
                session, yesterday_start, yesterday_end
            )
            report_data["service_costs"] = service_costs

            # Add comprehensive admin metrics
            security_metrics = await generate_security_metrics(session, yesterday_start, yesterday_end)
            system_health = await generate_system_health_metrics(session, yesterday_start, yesterday_end)
            user_behavior = await generate_user_behavior_metrics(session, yesterday_start, yesterday_end)
            operational_metrics = await generate_operational_metrics(session, yesterday_start, yesterday_end)
            risk_management = await generate_risk_management_metrics(session, yesterday_start, yesterday_end)
            
            # Add new comprehensive metrics
            action_items = await generate_action_items(session, yesterday_start, yesterday_end)
            balance_summary = await generate_balance_summary()
            error_analysis = await generate_error_analysis(session, yesterday_start, yesterday_end)
            platform_uptime = await generate_platform_uptime(session, yesterday_start, yesterday_end)
            support_metrics = await generate_support_metrics(session, yesterday_start, yesterday_end)
            growth_metrics = await generate_growth_metrics(session, yesterday_start, yesterday_end)
            
            # Add low balance warnings to action items
            if balance_summary.get("fincra_ngn", {}).get("alert_level"):
                fincra_balance = balance_summary["fincra_ngn"]
                action_items["items"].append({
                    "severity": "high" if fincra_balance["alert_level"] in ["CRITICAL", "EMERGENCY", "OPERATIONAL_DANGER"] else "medium",
                    "type": "low_balance",
                    "message": f"Fincra NGN balance low: {fincra_balance['formatted']} - Status: {fincra_balance['status']}"
                })
                action_items["total_count"] += 1
                if fincra_balance["alert_level"] in ["CRITICAL", "EMERGENCY", "OPERATIONAL_DANGER"]:
                    action_items["has_critical"] = True
            
            if balance_summary.get("kraken_usd", {}).get("alert_level"):
                kraken_balance = balance_summary["kraken_usd"]
                action_items["items"].append({
                    "severity": "high" if kraken_balance["alert_level"] in ["CRITICAL", "EMERGENCY", "OPERATIONAL_DANGER"] else "medium",
                    "type": "low_balance",
                    "message": f"Kraken USD balance low: {kraken_balance['formatted']} - Status: {kraken_balance['status']}"
                })
                action_items["total_count"] += 1
                if kraken_balance["alert_level"] in ["CRITICAL", "EMERGENCY", "OPERATIONAL_DANGER"]:
                    action_items["has_critical"] = True
            
            report_data.update({
                "security_metrics": security_metrics,
                "system_health": system_health,
                "user_behavior": user_behavior,
                "operational_metrics": operational_metrics,
                "risk_management": risk_management,
                "action_items": action_items,
                "balance_summary": balance_summary,
                "error_analysis": error_analysis,
                "platform_uptime": platform_uptime,
                "support_metrics": support_metrics,
                "growth_metrics": growth_metrics
            })

            # Generate executive summary AFTER all other metrics
            executive_summary = generate_executive_summary(report_data)
            report_data["executive_summary"] = executive_summary

            # Send email report
            email_service = EmailService()

            html_content = generate_report_html(report_data, yesterday)
            subject = f"📊 Daily Financial Report - {yesterday.strftime('%B %d, %Y')}"

            success = email_service.send_email(
                to_email=admin_email,
                subject=subject,
                html_content=html_content,
            )

            if success:
                logger.info(
                    f"✅ Daily financial report sent successfully to {admin_email}"
                )
                return {"status": "completed", "email_sent": True, "recipient": admin_email}
            else:
                logger.error(f"❌ Failed to send daily financial report to {admin_email}")
                return {"status": "email_failed", "email_sent": False, "recipient": admin_email}

        finally:
            session.close()

    except Exception as e:
        logger.error(f"❌ Error generating daily financial report: {e}")
        return {"status": "error", "error": str(e)}


# Backwards compatibility alias for legacy code
async def send_daily_financial_report():
    """Legacy alias for generate_daily_financial_report - DO NOT USE, will be removed"""
    return await generate_daily_financial_report()


async def generate_financial_metrics(session, start_date, end_date):
    """Generate comprehensive financial metrics for the date range"""

    # Get previous day for comparison
    prev_day_start = start_date - timedelta(days=1)
    prev_day_end = end_date - timedelta(days=1)

    # Escrow metrics
    escrows_created = (
        session.query(Escrow)
        .filter(and_(Escrow.created_at >= start_date, Escrow.created_at <= end_date))
        .count()
    )

    escrows_completed = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= start_date,
                Escrow.updated_at <= end_date,
            )
        )
        .count()
    )

    # Revenue from completed escrows (platform fees)
    completed_escrows = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= start_date,
                Escrow.updated_at <= end_date,
            )
        )
        .all()
    )

    total_escrow_volume = Decimal("0")
    total_platform_fees = Decimal("0")

    for escrow in completed_escrows:
        if escrow.amount_usd:
            volume = Decimal(str(escrow.amount_usd))
            total_escrow_volume += volume
            # Platform fee calculation
            fee = volume * (Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100"))
            total_platform_fees += fee

    # Cashout metrics
    cashouts_requested = (
        session.query(Cashout)
        .filter(
            and_(Cashout.created_at >= start_date, Cashout.created_at <= end_date)
        )
        .count()
    )

    cashouts_completed = (
        session.query(Cashout)
        .filter(
            and_(
                Cashout.status == "completed",
                Cashout.created_at >= start_date,
                Cashout.created_at <= end_date,
            )
        )
        .count()
    )

    # User metrics
    new_users = (
        session.query(User)
        .filter(and_(User.created_at >= start_date, User.created_at <= end_date))
        .count()
    )

    # Monthly totals for comparison
    month_start = start_date.replace(day=1)
    monthly_escrows = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= month_start,
            )
        )
        .count()
    )

    monthly_volume = Decimal("0")
    monthly_fees = Decimal("0")

    monthly_completed = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= month_start,
            )
        )
        .all()
    )

    for escrow in monthly_completed:
        if escrow.amount_usd:
            volume = Decimal(str(escrow.amount_usd))
            monthly_volume += volume
            fee = volume * (Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100"))
            monthly_fees += fee

    # Previous day comparison metrics
    prev_escrows_completed = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= prev_day_start,
                Escrow.updated_at <= prev_day_end,
            )
        )
        .count()
    )

    prev_completed_escrows = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= prev_day_start,
                Escrow.updated_at <= prev_day_end,
            )
        )
        .all()
    )

    prev_total_volume = Decimal("0")
    prev_platform_fees = Decimal("0")

    for escrow in prev_completed_escrows:
        if escrow.amount_usd:
            volume = Decimal(str(escrow.amount_usd))
            prev_total_volume += volume
            fee = volume * (Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100"))
            prev_platform_fees += fee

    # Weekly forecast based on last 7 days trend
    week_ago = start_date - timedelta(days=7)
    weekly_escrows = (
        session.query(Escrow)
        .filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at >= week_ago,
            )
        )
        .all()
    )

    weekly_volume = Decimal("0")
    weekly_fees = Decimal("0")
    for escrow in weekly_escrows:
        if escrow.amount_usd:
            volume = Decimal(str(escrow.amount_usd))
            weekly_volume += volume
            fee = volume * (Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100"))
            weekly_fees += fee

    # Calculate weekly forecast (7-day average projected)
    daily_avg_volume = float(weekly_volume) / 7 if weekly_volume > 0 else 0
    daily_avg_fees = float(weekly_fees) / 7 if weekly_fees > 0 else 0
    weekly_forecast_volume = daily_avg_volume * 7
    weekly_forecast_fees = daily_avg_fees * 7

    return {
        "date_range": {"start": start_date, "end": end_date},
        "daily": {
            "escrows_created": escrows_created,
            "escrows_completed": escrows_completed,
            "total_volume": float(total_escrow_volume),
            "platform_fees": float(total_platform_fees),
            "cashouts_requested": cashouts_requested,
            "cashouts_completed": cashouts_completed,
            "new_users": new_users,
        },
        "previous_day": {
            "escrows_completed": prev_escrows_completed,
            "total_volume": float(prev_total_volume),
            "platform_fees": float(prev_platform_fees),
        },
        "weekly_forecast": {
            "projected_volume": weekly_forecast_volume,
            "projected_fees": weekly_forecast_fees,
            "based_on_days": 7,
        },
        "monthly": {
            "escrows_completed": monthly_escrows,
            "total_volume": float(monthly_volume),
            "platform_fees": float(monthly_fees),
        },
    }


async def generate_security_metrics(session, start_date, end_date):
    """Generate security and risk metrics"""
    try:
        # Dispute cases (using dispute status and dispute_reason)
        disputes_opened = session.query(Escrow).filter(
            and_(
                Escrow.status == EscrowStatus.DISPUTED.value,
                Escrow.updated_at >= start_date,
                Escrow.updated_at <= end_date
            )
        ).count()

        # Failed transactions (payment timeouts, cancellations)
        failed_transactions = session.query(Escrow).filter(
            and_(
                Escrow.status.in_([EscrowStatus.CANCELLED.value, EscrowStatus.EXPIRED.value]),
                Escrow.updated_at >= start_date,
                Escrow.updated_at <= end_date
            )
        ).count()

        # High-value alerts (stablecoin transactions above $1000)
        high_value_threshold = Decimal("1000.0")
        high_value_transactions = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date,
                Escrow.currency.in_(['USDT', 'USDC', 'USDT-TRC20', 'USDT-ERC20', 'BUSD', 'DAI']),
                Escrow.amount >= high_value_threshold
            )
        ).count()

        # Failed cashouts
        failed_cashouts = session.query(Cashout).filter(
            and_(
                Cashout.created_at >= start_date,
                Cashout.created_at <= end_date,
                Cashout.status.in_(["failed", "declined"])
            )
        ).count()

        return {
            "disputes_opened": disputes_opened,
            "failed_transactions": failed_transactions,
            "high_value_transactions": high_value_transactions,
            "failed_cashouts": failed_cashouts,
            "risk_score": min(100, (failed_transactions * 10) + (disputes_opened * 20))
        }
    except Exception as e:
        logger.error(f"Error generating security metrics: {e}")
        return {
            "disputes_opened": 0,
            "failed_transactions": 0,
            "high_value_transactions": 0,
            "failed_cashouts": 0,
            "risk_score": 0,
            "error": str(e)
        }


async def generate_system_health_metrics(session, start_date, end_date):
    """Generate system health and uptime metrics"""
    try:
        # Check for recent errors in escrow/cashout processing
        
        # Check for stuck escrows (created more than 24 hours ago, still pending)
        day_ago = end_date - timedelta(hours=24)
        stuck_escrows = session.query(Escrow).filter(
            and_(
                Escrow.status == EscrowStatus.PAYMENT_PENDING.value,
                Escrow.created_at <= day_ago
            )
        ).count()

        # Check for pending cashouts requiring admin approval
        pending_admin_cashouts = session.query(Cashout).filter(
            and_(
                Cashout.status == "pending",
                Cashout.admin_approved == False
            )
        ).count()

        # Webhook success estimation (based on successful vs failed transactions)
        total_escrows_today = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date
            )
        ).count()
        
        successful_escrows = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date,
                Escrow.status.in_([EscrowStatus.COMPLETED.value, EscrowStatus.ACTIVE.value])
            )
        ).count()
        
        webhook_success_rate = (successful_escrows / total_escrows_today * 100) if total_escrows_today > 0 else 100

        return {
            "stuck_escrows": stuck_escrows,
            "pending_admin_cashouts": pending_admin_cashouts,
            "webhook_success_rate": round(webhook_success_rate, 1),
            "system_status": "Healthy" if stuck_escrows < 5 and pending_admin_cashouts < 10 else "Attention Required"
        }
    except Exception as e:
        logger.error(f"Error generating system health metrics: {e}")
        return {
            "stuck_escrows": 0,
            "pending_admin_cashouts": 0,
            "webhook_success_rate": 0,
            "system_status": "Error",
            "error": str(e)
        }


async def generate_user_behavior_metrics(session, start_date, end_date):
    """Generate user behavior and engagement metrics"""
    try:
        # Daily active users (users with escrow activity)
        daily_active_users = session.query(func.count(func.distinct(Escrow.buyer_id))).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date
            )
        ).scalar() or 0

        # Average transaction size (filter by stablecoins for accurate USD calculation)
        avg_transaction_size = session.query(func.avg(Escrow.amount)).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date,
                Escrow.currency.in_(['USDT', 'USDC', 'USDT-TRC20', 'USDT-ERC20', 'BUSD', 'DAI'])
            )
        ).scalar() or 0

        # Popular cryptocurrencies
        currency_usage = session.query(
            Escrow.currency, 
            func.count(Escrow.id).label('count')
        ).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date
            )
        ).group_by(Escrow.currency).order_by(desc('count')).limit(5).all()

        popular_currencies = [{"currency": row[0], "count": row[1]} for row in currency_usage]

        # User retention (users who made escrows yesterday vs today)
        yesterday_start = start_date - timedelta(days=1)
        yesterday_end = end_date - timedelta(days=1)
        
        yesterday_users = set(session.query(Escrow.buyer_id).filter(
            and_(
                Escrow.created_at >= yesterday_start,
                Escrow.created_at <= yesterday_end
            )
        ).distinct().all())
        
        today_users = set(session.query(Escrow.buyer_id).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date
            )
        ).distinct().all())
        
        returning_users = len(yesterday_users.intersection(today_users))

        return {
            "daily_active_users": daily_active_users,
            "average_transaction_size": float(avg_transaction_size) if avg_transaction_size else 0,
            "popular_currencies": popular_currencies,
            "returning_users": returning_users
        }
    except Exception as e:
        logger.error(f"Error generating user behavior metrics: {e}")
        return {
            "daily_active_users": 0,
            "average_transaction_size": 0,
            "popular_currencies": [],
            "returning_users": 0,
            "error": str(e)
        }


async def generate_operational_metrics(session, start_date, end_date):
    """Generate operational efficiency metrics"""
    try:
        # Average escrow completion time
        completed_escrows = session.query(Escrow).filter(
            and_(
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.completed_at >= start_date,
                Escrow.completed_at <= end_date,
                Escrow.created_at.isnot(None),
                Escrow.completed_at.isnot(None)
            )
        ).all()

        if completed_escrows:
            completion_times = []
            for escrow in completed_escrows:
                if escrow.created_at and escrow.completed_at:
                    completion_time = (escrow.completed_at - escrow.created_at).total_seconds() / 3600  # hours
                    completion_times.append(completion_time)
            
            avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0
        else:
            avg_completion_time = 0

        # Admin interventions (manual cashout approvals)
        admin_interventions = session.query(Cashout).filter(
            and_(
                Cashout.created_at >= start_date,
                Cashout.created_at <= end_date,
                Cashout.admin_approved == True
            )
        ).count()

        # Auto-cashout success rate
        total_cashouts = session.query(Cashout).filter(
            and_(
                Cashout.created_at >= start_date,
                Cashout.created_at <= end_date
            )
        ).count()

        auto_cashouts = session.query(Cashout).filter(
            and_(
                Cashout.created_at >= start_date,
                Cashout.created_at <= end_date,
                Cashout.admin_approved == False,
                Cashout.status == "completed"
            )
        ).count()

        auto_cashout_success_rate = (auto_cashouts / total_cashouts * 100) if total_cashouts > 0 else 0

        # Pending actions requiring admin attention
        pending_actions = session.query(Cashout).filter(
            and_(
                Cashout.status == "pending",
                Cashout.admin_approved == False
            )
        ).count()

        return {
            "avg_completion_time_hours": round(avg_completion_time, 2),
            "admin_interventions": admin_interventions,
            "auto_cashout_success_rate": round(auto_cashout_success_rate, 1),
            "pending_admin_actions": pending_actions
        }
    except Exception as e:
        logger.error(f"Error generating operational metrics: {e}")
        return {
            "avg_completion_time_hours": 0,
            "admin_interventions": 0,
            "auto_cashout_success_rate": 0,
            "pending_admin_actions": 0,
            "error": str(e)
        }


async def generate_risk_management_metrics(session, start_date, end_date):
    """Generate financial risk management metrics"""
    try:
        # Large pending escrows (stablecoins >$500)
        large_pending_threshold = Decimal("500.0")
        large_pending_escrows = session.query(Escrow).filter(
            and_(
                Escrow.status.in_([EscrowStatus.PAYMENT_PENDING.value, EscrowStatus.ACTIVE.value]),
                Escrow.currency.in_(['USDT', 'USDC', 'USDT-TRC20', 'USDT-ERC20', 'BUSD', 'DAI']),
                Escrow.amount >= large_pending_threshold
            )
        ).all()

        large_pending_count = len(large_pending_escrows)
        large_pending_value = sum(float(escrow.amount_usd or 0) for escrow in large_pending_escrows)

        # Stuck transactions (older than 24 hours, not completed)
        day_ago = end_date - timedelta(hours=24)
        stuck_transactions = session.query(Escrow).filter(
            and_(
                Escrow.created_at <= day_ago,
                Escrow.status.in_([EscrowStatus.PAYMENT_PENDING.value, EscrowStatus.ACTIVE.value])
            )
        ).count()

        # Currency exposure (distribution of pending values by currency)
        # Only include stablecoins for accurate USD calculation
        currency_exposure = session.query(
            Escrow.currency,
            func.sum(Escrow.amount).label('total_usd'),
            func.count(Escrow.id).label('count')
        ).filter(
            and_(
                Escrow.status.in_([EscrowStatus.PAYMENT_PENDING.value, EscrowStatus.ACTIVE.value]),
                Escrow.currency.in_(['USDT', 'USDC', 'USDT-TRC20', 'USDT-ERC20', 'BUSD', 'DAI'])
            )
        ).group_by(Escrow.currency).all()

        exposure_breakdown = [
            {
                "currency": row[0], 
                "total_usd": float(row[1] or 0), 
                "count": row[2]
            } for row in currency_exposure
        ]

        # Risk score calculation
        risk_factors = [
            large_pending_count * 5,  # Large pending escrows
            stuck_transactions * 10,  # Stuck transactions
            len(exposure_breakdown) * 2  # Currency diversification (more currencies = slight risk)
        ]
        overall_risk_score = min(100, sum(risk_factors))

        return {
            "large_pending_escrows": {
                "count": large_pending_count,
                "total_value_usd": large_pending_value
            },
            "stuck_transactions": stuck_transactions,
            "currency_exposure": exposure_breakdown,
            "overall_risk_score": overall_risk_score
        }
    except Exception as e:
        logger.error(f"Error generating risk management metrics: {e}")
        return {
            "large_pending_escrows": {"count": 0, "total_value_usd": 0},
            "stuck_transactions": 0,
            "currency_exposure": [],
            "overall_risk_score": 0,
            "error": str(e)
        }


async def generate_action_items(session, start_date, end_date):
    """Generate critical action items requiring admin attention"""
    try:
        action_items = []
        
        # 1. Cashouts pending >24h (admin approval needed)
        day_ago = end_date - timedelta(hours=24)
        pending_cashouts = session.query(Cashout).filter(
            and_(
                Cashout.created_at <= day_ago,
                Cashout.status.in_(["pending", "admin_pending", "awaiting_response"]),
                Cashout.admin_approved == False
            )
        ).all()
        
        if pending_cashouts:
            for cashout in pending_cashouts:
                hours_pending = (end_date - cashout.created_at).total_seconds() / 3600
                action_items.append({
                    "severity": "high" if hours_pending > 48 else "medium",
                    "type": "cashout_pending",
                    "message": f"Cashout {cashout.id} pending {hours_pending:.1f}h - ${cashout.amount_usd:.2f} {cashout.currency}",
                    "hours_pending": hours_pending
                })
        
        # 2. Stuck escrows >48h
        two_days_ago = end_date - timedelta(hours=48)
        stuck_escrows = session.query(Escrow).filter(
            and_(
                Escrow.created_at <= two_days_ago,
                Escrow.status.in_([
                    EscrowStatus.PAYMENT_PENDING.value,
                    EscrowStatus.AWAITING_SELLER.value,
                    EscrowStatus.PENDING_SELLER.value
                ])
            )
        ).all()
        
        if stuck_escrows:
            for escrow in stuck_escrows:
                hours_stuck = (end_date - escrow.created_at).total_seconds() / 3600
                action_items.append({
                    "severity": "high",
                    "type": "escrow_stuck",
                    "message": f"Escrow {escrow.escrow_id} stuck {hours_stuck:.1f}h - ${escrow.amount_usd:.2f}",
                    "hours_stuck": hours_stuck
                })
        
        # 3. Low balance warnings will be handled by balance_summary
        # We'll add action items based on balance status
        
        # 4. Active disputes requiring attention
        active_disputes = session.query(Dispute).filter(
            Dispute.status.in_([DisputeStatus.OPEN.value, DisputeStatus.UNDER_REVIEW.value])
        ).all()
        
        if active_disputes:
            for dispute in active_disputes:
                action_items.append({
                    "severity": "high",
                    "type": "dispute_active",
                    "message": f"Dispute {dispute.id} on escrow {dispute.escrow_id} - Status: {dispute.status}",
                    "dispute_id": dispute.id
                })
        
        return {
            "total_count": len(action_items),
            "items": action_items,
            "has_critical": any(item["severity"] == "high" for item in action_items)
        }
        
    except Exception as e:
        logger.error(f"Error generating action items: {e}")
        return {
            "total_count": 0,
            "items": [],
            "has_critical": False,
            "error": str(e)
        }


async def generate_balance_summary():
    """Get current balances from BalanceGuard"""
    try:
        # Use BalanceGuard's monitor_all_balances function
        balance_result = await monitor_all_balances()
        
        balances = {}
        
        # Extract Fincra NGN balance
        fincra_snapshot = None
        kraken_snapshot = None
        
        for snapshot_dict in balance_result.get("balance_snapshots", []):
            if snapshot_dict.get("provider") == "fincra":
                fincra_snapshot = snapshot_dict
            elif snapshot_dict.get("provider") == "kraken":
                kraken_snapshot = snapshot_dict
        
        # Process Fincra balance
        if fincra_snapshot:
            alert_level = fincra_snapshot.get("alert_level")
            balances["fincra_ngn"] = {
                "balance": fincra_snapshot.get("balance", 0),
                "formatted": fincra_snapshot.get("formatted_balance", "₦0.00"),
                "status": "healthy" if not alert_level else alert_level.lower(),
                "alert_level": alert_level
            }
        else:
            balances["fincra_ngn"] = {
                "balance": 0,
                "formatted": "₦0.00",
                "status": "unknown",
                "alert_level": None
            }
        
        # Process Kraken balance
        if kraken_snapshot:
            alert_level = kraken_snapshot.get("alert_level")
            balances["kraken_usd"] = {
                "balance": kraken_snapshot.get("balance", 0),
                "formatted": kraken_snapshot.get("formatted_balance", "$0.00"),
                "status": "healthy" if not alert_level else alert_level.lower(),
                "alert_level": alert_level
            }
        else:
            balances["kraken_usd"] = {
                "balance": 0,
                "formatted": "$0.00",
                "status": "unknown",
                "alert_level": None
            }
        
        return balances
        
    except Exception as e:
        logger.error(f"Error generating balance summary: {e}")
        return {
            "fincra_ngn": {"balance": 0, "formatted": "₦0.00", "status": "error", "alert_level": None},
            "kraken_usd": {"balance": 0, "formatted": "$0.00", "status": "error", "alert_level": None},
            "error": str(e)
        }


async def generate_error_analysis(session, start_date, end_date):
    """Analyze transaction failures and extract top failure reasons"""
    try:
        # Query cancelled and expired escrows
        failed_escrows = session.query(Escrow).filter(
            and_(
                Escrow.status.in_([EscrowStatus.CANCELLED.value, EscrowStatus.EXPIRED.value]),
                Escrow.updated_at >= start_date,
                Escrow.updated_at <= end_date
            )
        ).all()
        
        # Count failure reasons (using status as the reason)
        reason_counts = {}
        total_failures = len(failed_escrows)
        
        for escrow in failed_escrows:
            reason = escrow.status or "Unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        # Sort by count and get top 5
        sorted_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Calculate percentages
        top_failures = []
        for reason, count in sorted_reasons:
            percentage = (count / total_failures * 100) if total_failures > 0 else 0
            top_failures.append({
                "reason": reason,
                "count": count,
                "percentage": round(percentage, 1)
            })
        
        return {
            "total_failures": total_failures,
            "top_failures": top_failures,
            "has_failures": total_failures > 0
        }
        
    except Exception as e:
        logger.error(f"Error generating error analysis: {e}")
        return {
            "total_failures": 0,
            "top_failures": [],
            "has_failures": False,
            "error": str(e)
        }


async def generate_platform_uptime(session, start_date, end_date):
    """Generate platform uptime and performance metrics"""
    try:
        # Calculate error rate from successful vs failed transactions
        total_transactions = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date
            )
        ).count()
        
        failed_transactions = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date,
                Escrow.status.in_([EscrowStatus.CANCELLED.value, EscrowStatus.EXPIRED.value, EscrowStatus.PAYMENT_FAILED.value])
            )
        ).count()
        
        successful_transactions = total_transactions - failed_transactions
        
        # Calculate rates
        success_rate = (successful_transactions / total_transactions * 100) if total_transactions > 0 else 100
        error_rate = (failed_transactions / total_transactions * 100) if total_transactions > 0 else 0
        
        # Estimate API response times from completed escrows
        # Use the time between creation and first status update as a proxy
        completed_escrows = session.query(Escrow).filter(
            and_(
                Escrow.created_at >= start_date,
                Escrow.created_at <= end_date,
                Escrow.status == EscrowStatus.COMPLETED.value,
                Escrow.updated_at.isnot(None)
            )
        ).limit(100).all()  # Sample last 100
        
        response_times = []
        for escrow in completed_escrows:
            if escrow.created_at and escrow.updated_at:
                response_time = (escrow.updated_at - escrow.created_at).total_seconds()
                if response_time > 0 and response_time < 3600:  # Within 1 hour (filter outliers)
                    response_times.append(response_time)
        
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        return {
            "success_rate": round(success_rate, 2),
            "error_rate": round(error_rate, 2),
            "availability": round(success_rate, 2),  # Using success rate as availability proxy
            "avg_response_time_seconds": round(avg_response_time, 2),
            "total_transactions": total_transactions,
            "successful_transactions": successful_transactions,
            "failed_transactions": failed_transactions
        }
        
    except Exception as e:
        logger.error(f"Error generating platform uptime metrics: {e}")
        return {
            "success_rate": 0,
            "error_rate": 0,
            "availability": 0,
            "avg_response_time_seconds": 0,
            "total_transactions": 0,
            "successful_transactions": 0,
            "failed_transactions": 0,
            "error": str(e)
        }


async def generate_support_metrics(session, start_date, end_date):
    """Generate customer support metrics"""
    try:
        # Active disputes count
        active_disputes = session.query(Dispute).filter(
            Dispute.status.in_([DisputeStatus.OPEN.value, DisputeStatus.UNDER_REVIEW.value])
        ).count()
        
        # Disputes opened today
        disputes_opened_today = session.query(Dispute).filter(
            and_(
                Dispute.created_at >= start_date,
                Dispute.created_at <= end_date
            )
        ).count()
        
        # Disputes resolved today
        disputes_resolved_today = session.query(Dispute).filter(
            and_(
                Dispute.resolved_at >= start_date,
                Dispute.resolved_at <= end_date,
                Dispute.status == DisputeStatus.RESOLVED.value
            )
        ).count()
        
        # Calculate average response time for resolved disputes
        resolved_disputes = session.query(Dispute).filter(
            and_(
                Dispute.resolved_at >= start_date,
                Dispute.resolved_at <= end_date,
                Dispute.status == DisputeStatus.RESOLVED.value,
                Dispute.created_at.isnot(None),
                Dispute.resolved_at.isnot(None)
            )
        ).all()
        
        response_times = []
        for dispute in resolved_disputes:
            if dispute.created_at and dispute.resolved_at:
                response_time = (dispute.resolved_at - dispute.created_at).total_seconds() / 3600  # hours
                response_times.append(response_time)
        
        avg_response_time_hours = sum(response_times) / len(response_times) if response_times else 0
        
        # Pending support actions (active disputes + pending cashouts)
        pending_cashouts = session.query(Cashout).filter(
            Cashout.status.in_(["pending", "admin_pending", "awaiting_response"])
        ).count()
        
        pending_actions = active_disputes + pending_cashouts
        
        return {
            "active_disputes": active_disputes,
            "disputes_opened_today": disputes_opened_today,
            "disputes_resolved_today": disputes_resolved_today,
            "avg_response_time_hours": round(avg_response_time_hours, 2),
            "pending_actions": pending_actions
        }
        
    except Exception as e:
        logger.error(f"Error generating support metrics: {e}")
        return {
            "active_disputes": 0,
            "disputes_opened_today": 0,
            "disputes_resolved_today": 0,
            "avg_response_time_hours": 0,
            "pending_actions": 0,
            "error": str(e)
        }


async def generate_growth_metrics(session, start_date, end_date):
    """Generate user growth and retention metrics"""
    try:
        # Users active today (created escrow today)
        today_active_users = set(
            user_id[0] for user_id in session.query(Escrow.buyer_id).filter(
                and_(
                    Escrow.created_at >= start_date,
                    Escrow.created_at <= end_date
                )
            ).distinct().all()
        )
        
        # Users active 7 days ago
        week_ago_start = start_date - timedelta(days=7)
        week_ago_end = end_date - timedelta(days=7)
        
        week_ago_active_users = set(
            user_id[0] for user_id in session.query(Escrow.buyer_id).filter(
                and_(
                    Escrow.created_at >= week_ago_start,
                    Escrow.created_at <= week_ago_end
                )
            ).distinct().all()
        )
        
        # Calculate retention rate (users active 7 days ago who are still active today)
        retained_users = today_active_users.intersection(week_ago_active_users)
        retention_rate = (len(retained_users) / len(week_ago_active_users) * 100) if week_ago_active_users else 0
        
        # Calculate churn rate
        churned_users = len(week_ago_active_users) - len(retained_users)
        churn_rate = (churned_users / len(week_ago_active_users) * 100) if week_ago_active_users else 0
        
        # Week-over-week user growth
        wow_growth = ((len(today_active_users) - len(week_ago_active_users)) / len(week_ago_active_users) * 100) if week_ago_active_users else 0
        
        # Total registered users
        total_users = session.query(User).count()
        
        # New users this week
        week_start = start_date - timedelta(days=7)
        new_users_this_week = session.query(User).filter(
            and_(
                User.created_at >= week_start,
                User.created_at <= end_date
            )
        ).count()
        
        return {
            "active_users_today": len(today_active_users),
            "active_users_week_ago": len(week_ago_active_users),
            "retained_users": len(retained_users),
            "retention_rate": round(retention_rate, 2),
            "churn_rate": round(churn_rate, 2),
            "wow_growth": round(wow_growth, 2),
            "total_users": total_users,
            "new_users_this_week": new_users_this_week
        }
        
    except Exception as e:
        logger.error(f"Error generating growth metrics: {e}")
        return {
            "active_users_today": 0,
            "active_users_week_ago": 0,
            "retained_users": 0,
            "retention_rate": 0,
            "churn_rate": 0,
            "wow_growth": 0,
            "total_users": 0,
            "new_users_this_week": 0,
            "error": str(e)
        }


def generate_executive_summary(report_data):
    """Generate executive summary from all collected metrics"""
    try:
        daily = report_data.get("daily", {})
        previous_day = report_data.get("previous_day", {})
        security_metrics = report_data.get("security_metrics", {})
        system_health = report_data.get("system_health", {})
        action_items = report_data.get("action_items", {})
        
        # Calculate revenue change %
        current_revenue = daily.get("platform_fees", 0)
        previous_revenue = previous_day.get("platform_fees", 0)
        
        if previous_revenue > 0:
            revenue_change_pct = ((current_revenue - previous_revenue) / previous_revenue) * 100
        elif current_revenue > 0:
            revenue_change_pct = 100.0
        else:
            revenue_change_pct = 0.0
        
        # Calculate volume change %
        current_volume = daily.get("total_volume", 0)
        previous_volume = previous_day.get("total_volume", 0)
        
        if previous_volume > 0:
            volume_change_pct = ((current_volume - previous_volume) / previous_volume) * 100
        elif current_volume > 0:
            volume_change_pct = 100.0
        else:
            volume_change_pct = 0.0
        
        # Determine risk level from security metrics risk_score
        risk_score = security_metrics.get("risk_score", 0)
        if risk_score < 30:
            risk_level = "Low"
        elif risk_score < 70:
            risk_level = "Medium"
        else:
            risk_level = "High"
        
        # Extract system status
        system_status = system_health.get("system_status", "Unknown")
        
        # Extract action items count
        action_count = action_items.get("total_count", 0)
        has_critical = action_items.get("has_critical", False)
        
        return {
            "revenue_change_pct": round(revenue_change_pct, 2),
            "volume_change_pct": round(volume_change_pct, 2),
            "risk_level": risk_level,
            "risk_score": risk_score,
            "system_status": system_status,
            "action_count": action_count,
            "has_critical": has_critical,
            "current_revenue": current_revenue,
            "current_volume": current_volume
        }
        
    except Exception as e:
        logger.error(f"Error generating executive summary: {e}")
        return {
            "revenue_change_pct": 0,
            "volume_change_pct": 0,
            "risk_level": "Unknown",
            "risk_score": 0,
            "system_status": "Error",
            "action_count": 0,
            "has_critical": False,
            "current_revenue": 0,
            "current_volume": 0,
            "error": str(e)
        }


def generate_report_html(data, report_date):
    """Generate HTML email content for financial report"""

    daily = data["daily"]
    previous_day = data["previous_day"]
    weekly_forecast = data["weekly_forecast"]
    monthly = data["monthly"]
    service_costs = data.get("service_costs", {})
    action_items = data.get("action_items", {})
    balance_summary = data.get("balance_summary", {})
    error_analysis = data.get("error_analysis", {})
    growth_metrics = data.get("growth_metrics", {})
    support_metrics = data.get("support_metrics", {})
    platform_uptime = data.get("platform_uptime", {})
    security = data.get("security_metrics", {})

    # Calculate percentage changes from previous day
    def calculate_change_percentage(current, previous):
        if previous == 0:
            return "New" if current > 0 else "0%"
        change = ((current - previous) / previous) * 100
        return f"+{change:.1f}%" if change > 0 else f"{change:.1f}%"

    revenue_change = calculate_change_percentage(
        daily["platform_fees"], previous_day["platform_fees"]
    )
    volume_change = calculate_change_percentage(
        daily["total_volume"], previous_day["total_volume"]
    )
    
    # Determine risk level
    risk_score = security.get("risk_score", 0)
    risk_level = "🟢 Low" if risk_score < 30 else "🟡 Medium" if risk_score < 70 else "🔴 High"
    risk_color = "#28a745" if risk_score < 30 else "#ffc107" if risk_score < 70 else "#dc3545"
    
    # Determine system health status
    system_health = data.get("system_health", {})
    system_status = system_health.get("system_status", "Unknown")
    status_color = "#28a745" if system_status == "Healthy" else "#ffc107" if system_status == "Attention Required" else "#dc3545"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background: #2c3e50; color: white; padding: 20px; border-radius: 8px; }}
            .metric {{ background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #007bff; }}
            .metric h3 {{ margin: 0 0 10px 0; color: #2c3e50; }}
            .metric .value {{ font-size: 24px; font-weight: bold; color: #007bff; }}
            .metric .change {{ font-size: 14px; margin-top: 5px; font-weight: bold; }}
            .change.positive {{ color: #28a745; }}
            .change.negative {{ color: #dc3545; }}
            .change.neutral {{ color: #6c757d; }}
            .section {{ margin: 20px 0; }}
            .section h2 {{ color: #2c3e50; border-bottom: 2px solid #007bff; padding-bottom: 5px; }}
            .revenue {{ border-left-color: #28a745; }}
            .revenue .value {{ color: #28a745; }}
            .forecast {{ border-left-color: #ffc107; }}
            .forecast .value {{ color: #ffc107; }}
            .warning {{ border-left-color: #dc3545; background: #fff5f5; }}
            .warning .value {{ color: #dc3545; }}
            .critical {{ background: #fff5f5; border-left-color: #dc3545; }}
            .action-item {{ padding: 10px; margin: 5px 0; border-radius: 5px; }}
            .action-high {{ background: #ffebee; border-left: 3px solid #dc3545; }}
            .action-medium {{ background: #fff3e0; border-left: 3px solid #ff9800; }}
            .executive-summary {{ background: #e3f2fd; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 5px solid #2196f3; }}
            .balance-healthy {{ color: #28a745; }}
            .balance-warning {{ color: #ff9800; }}
            .balance-critical {{ color: #dc3545; }}
            .error-bar {{ background: #dc3545; height: 20px; border-radius: 3px; margin: 5px 0; }}
            .footer {{ margin-top: 30px; padding: 15px; background: #f8f9fa; border-radius: 8px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📊 Daily Financial Report</h1>
            <h2>{Config.PLATFORM_NAME} - {report_date.strftime('%B %d, %Y')}</h2>
        </div>
        
        <!-- EXECUTIVE SUMMARY (NEW) -->
        <div class="executive-summary">
            <h2 style="margin-top: 0; color: #1976d2;">📋 Executive Summary</h2>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 10px; width: 50%;">
                        <strong>💰 Revenue Today:</strong> ${daily['platform_fees']:.2f}
                        <span style="color: {'#28a745' if '+' in revenue_change else '#dc3545'}; font-weight: bold;"> ({revenue_change})</span>
                    </td>
                    <td style="padding: 10px; width: 50%;">
                        <strong>💸 Volume Today:</strong> ${daily['total_volume']:.2f}
                        <span style="color: {'#28a745' if '+' in volume_change else '#dc3545'}; font-weight: bold;"> ({volume_change})</span>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 10px;">
                        <strong>⚠️ Risk Level:</strong> <span style="color: {risk_color}; font-weight: bold;">{risk_level}</span>
                    </td>
                    <td style="padding: 10px;">
                        <strong>🔧 System Health:</strong> <span style="color: {status_color}; font-weight: bold;">{system_status}</span>
                    </td>
                </tr>
                <tr>
                    <td colspan="2" style="padding: 10px;">
                        <strong>📝 Action Items:</strong> <span style="color: {'#dc3545' if action_items.get('has_critical') else '#28a745'}; font-weight: bold;">{action_items.get('total_count', 0)} items</span>
                        {' - <span style="color: #dc3545;">⚠️ CRITICAL ATTENTION REQUIRED</span>' if action_items.get('has_critical') else ''}
                    </td>
                </tr>
            </table>
        </div>"""
    
    # Add Critical Action Items section if any exist
    if action_items.get("total_count", 0) > 0:
        html_content += """
        
        <div class="section">
            <h2>⚠️ CRITICAL ACTION ITEMS</h2>"""
        
        for item in action_items.get("items", []):
            severity_class = "action-high" if item["severity"] == "high" else "action-medium"
            severity_icon = "🔴" if item["severity"] == "high" else "🟡"
            html_content += f"""
            <div class="action-item {severity_class}">
                {severity_icon} <strong>{item['type'].upper()}:</strong> {item['message']}
            </div>"""
        
        html_content += """
        </div>"""
    
    # Add Current Balances section
    html_content += """
        
        <div class="section">
            <h2>💰 CURRENT BALANCES</h2>"""
    
    # Fincra NGN balance
    fincra = balance_summary.get("fincra_ngn", {})
    fincra_status = fincra.get("status", "unknown")
    fincra_class = "balance-healthy" if fincra_status == "healthy" else "balance-warning" if "warning" in fincra_status else "balance-critical"
    fincra_icon = "🟢" if fincra_status == "healthy" else "🟡" if "warning" in fincra_status else "🔴"
    
    html_content += f"""
            <div class="metric">
                <h3>🏦 Fincra NGN Balance</h3>
                <div class="value {fincra_class}">{fincra.get('formatted', '₦0.00')}</div>
                <p>{fincra_icon} Status: <span class="{fincra_class}">{fincra_status.upper()}</span></p>
            </div>"""
    
    # Kraken USD balance
    kraken = balance_summary.get("kraken_usd", {})
    kraken_status = kraken.get("status", "unknown")
    kraken_class = "balance-healthy" if kraken_status == "healthy" else "balance-warning" if "warning" in kraken_status else "balance-critical"
    kraken_icon = "🟢" if kraken_status == "healthy" else "🟡" if "warning" in kraken_status else "🔴"
    
    html_content += f"""
            <div class="metric">
                <h3>💱 Kraken USD Balance</h3>
                <div class="value {kraken_class}">{kraken.get('formatted', '$0.00')}</div>
                <p>{kraken_icon} Status: <span class="{kraken_class}">{kraken_status.upper()}</span></p>
            </div>
        </div>"""
    
    # Add Error Analysis section
    html_content += """
        
        <div class="section">
            <h2>❌ ERROR ANALYSIS</h2>"""
    
    if error_analysis.get("has_failures"):
        html_content += f"""
            <div class="metric">
                <h3>Total Failures</h3>
                <div class="value" style="color: #dc3545;">{error_analysis.get('total_failures', 0)}</div>
                <p>Failed transactions today</p>
            </div>
            
            <div class="metric">
                <h3>Top 5 Failure Reasons</h3>"""
        
        for failure in error_analysis.get("top_failures", []):
            bar_width = min(failure['percentage'], 100)
            html_content += f"""
                <div style="margin: 10px 0;">
                    <strong>{failure['reason']}</strong> - {failure['count']} ({failure['percentage']}%)
                    <div style="background: #e0e0e0; border-radius: 3px; height: 20px; margin-top: 5px;">
                        <div style="background: #dc3545; width: {bar_width}%; height: 100%; border-radius: 3px;"></div>
                    </div>
                </div>"""
        
        html_content += """
            </div>"""
    else:
        html_content += """
            <div class="metric">
                <h3>✅ No Failures</h3>
                <div class="value" style="color: #28a745;">0</div>
                <p>No failed transactions today</p>
            </div>"""
    
    html_content += f"""
        </div>
        
        <div class="section">
            <h2>📈 Daily Performance</h2>
            
            <div class="metric revenue">
                <h3>💰 Platform Revenue</h3>
                <div class="value">${daily['platform_fees']:.2f}</div>
                <div class="change">{revenue_change} from previous day</div>
                <p>From {daily['escrows_completed']} completed escrows</p>
            </div>
            
            <div class="metric">
                <h3>💸 Transaction Volume</h3>
                <div class="value">${daily['total_volume']:.2f}</div>
                <div class="change">{volume_change} from previous day</div>
                <p>Total value of completed transactions</p>
            </div>
            
            <div class="metric">
                <h3>🆕 New Escrows</h3>
                <div class="value">{daily['escrows_created']}</div>
                <p>Created yesterday</p>
            </div>
            
            <div class="metric">
                <h3>✅ Completed Escrows</h3>
                <div class="value">{daily['escrows_completed']}</div>
                <p>Successfully resolved</p>
            </div>
            
            <div class="metric">
                <h3>💳 Cashouts</h3>
                <div class="value">{daily['cashouts_completed']}/{daily['cashouts_requested']}</div>
                <p>Completed/Requested</p>
            </div>
            
            <div class="metric">
                <h3>👥 New Users</h3>
                <div class="value">{daily['new_users']}</div>
                <p>Joined yesterday</p>
            </div>
        </div>
        
        <div class="section">
            <h2>🔮 Weekly Forecast</h2>
            
            <div class="metric forecast">
                <h3>📈 Projected Weekly Revenue</h3>
                <div class="value">${weekly_forecast['projected_fees']:.2f}</div>
                <p>Based on {weekly_forecast['based_on_days']}-day trend analysis</p>
            </div>
            
            <div class="metric forecast">
                <h3>💹 Projected Weekly Volume</h3>
                <div class="value">${weekly_forecast['projected_volume']:.2f}</div>
                <p>Estimated transaction volume for this week</p>
            </div>
        </div>
        
        <div class="section">
            <h2>📅 Month-to-Date Performance</h2>
            
            <div class="metric revenue">
                <h3>💰 Monthly Revenue</h3>
                <div class="value">${monthly['platform_fees']:.2f}</div>
                <p>From {monthly['escrows_completed']} completed escrows</p>
            </div>
            
            <div class="metric">
                <h3>💸 Monthly Volume</h3>
                <div class="value">${monthly['total_volume']:.2f}</div>
                <p>Total transaction volume this month</p>
            </div>
        </div>
        
        <div class="section">
            <h2>💰 Daily Service Costs</h2>"""

    # Add service costs section if available
    if service_costs and "summary" in service_costs:
        costs_summary = service_costs["summary"]
        total_cost = costs_summary.get("total_daily_cost", 0)
        highest_cost_service = costs_summary.get("highest_cost_service", "N/A")

        html_content += f"""
            <div class="metric revenue">
                <h3>💸 Total Daily Costs</h3>
                <div class="value">${total_cost:.4f}</div>
                <p>Operational expenses for {report_date.strftime('%B %d, %Y')}</p>
            </div>
            
            <div class="metric">
                <h3>📈 Highest Cost Service</h3>
                <div class="value">{highest_cost_service}</div>
                <p>Primary expense driver</p>
            </div>"""

        # Add breakdown for each service
        services = [
            "sms_costs",
            "email_costs",
            "fincra_costs",
            "dynopay_costs",
            "kraken_costs",
        ]
        service_names = [
            "📱 SMS (Twilio)",
            "📧 Email (Brevo)",
            "🏦 Fincra (NGN)",
            "💳 DynoPay (Crypto)",
            "🔄 Kraken (Cashouts)",
        ]

        for service_key, service_name in zip(services, service_names):
            if service_key in service_costs:
                service_data = service_costs[service_key]
                cost = service_data.get("total_cost", 0)
                count = service_data.get(
                    "transaction_count", service_data.get("message_count", 0)
                )

                html_content += f"""
            <div class="metric">
                <h3>{service_name}</h3>
                <div class="value">${cost:.4f}</div>
                <p>{count} {'transactions' if 'transaction_count' in service_data else 'messages'} processed</p>
            </div>"""

        # Add cost efficiency metrics
        if daily.get("platform_fees", 0) > 0 and total_cost > 0:
            cost_efficiency = (daily["platform_fees"] / total_cost) * 100
            html_content += f"""
            <div class="metric forecast">
                <h3>⚡ Cost Efficiency Ratio</h3>
                <div class="value">{cost_efficiency:.1f}%</div>
                <p>Revenue vs Operating Costs</p>
            </div>"""
    else:
        html_content += """
            <div class="metric">
                <h3>⚠️ Cost Analysis Unavailable</h3>
                <div class="value">N/A</div>
                <p>Service cost data could not be calculated</p>
            </div>"""

    # Add Security & Risk Metrics section
    security = data.get("security_metrics", {})
    risk_level = "🟢 Low" if security.get("risk_score", 0) < 30 else "🟡 Medium" if security.get("risk_score", 0) < 70 else "🔴 High"
    
    html_content += f"""
        </div>
        
        <div class="section">
            <h2>🔒 Security & Risk Dashboard</h2>
            
            <div class="metric">
                <h3>Overall Risk Score</h3>
                <div class="value">{security.get("risk_score", 0)}/100</div>
                <p>{risk_level} Risk Level</p>
            </div>
            
            <div class="metric">
                <h3>🚨 Disputes Opened</h3>
                <div class="value">{security.get("disputes_opened", 0)}</div>
                <p>Cancelled with reasons</p>
            </div>
            
            <div class="metric">
                <h3>❌ Failed Transactions</h3>
                <div class="value">{security.get("failed_transactions", 0)}</div>
                <p>Timeouts & cancellations</p>
            </div>
            
            <div class="metric">
                <h3>⚠️ High-Value Alerts</h3>
                <div class="value">{security.get("high_value_transactions", 0)}</div>
                <p>Transactions >$1,000</p>
            </div>
        </div>
        
        <div class="section">
            <h2>⚡ System Health Dashboard</h2>"""
    
    # Add System Health section
    system_health = data.get("system_health", {})
    status_color = "🟢" if system_health.get("system_status") == "Healthy" else "🟡" if system_health.get("system_status") == "Attention Required" else "🔴"
    
    html_content += f"""
            <div class="metric">
                <h3>System Status</h3>
                <div class="value">{status_color}</div>
                <p>{system_health.get("system_status", "Unknown")}</p>
            </div>
            
            <div class="metric">
                <h3>📡 Webhook Success Rate</h3>
                <div class="value">{system_health.get("webhook_success_rate", 0)}%</div>
                <p>Payment processing success</p>
            </div>
            
            <div class="metric">
                <h3>🔗 Stuck Escrows</h3>
                <div class="value">{system_health.get("stuck_escrows", 0)}</div>
                <p>Pending >24 hours</p>
            </div>
            
            <div class="metric">
                <h3>⏳ Pending Admin Actions</h3>
                <div class="value">{system_health.get("pending_admin_cashouts", 0)}</div>
                <p>Cashouts awaiting approval</p>
            </div>
        </div>
        
        <div class="section">
            <h2>👥 User Behavior Insights</h2>"""
    
    # Add User Behavior section
    user_behavior = data.get("user_behavior", {})
    
    html_content += f"""
            <div class="metric">
                <h3>👤 Daily Active Users</h3>
                <div class="value">{user_behavior.get("daily_active_users", 0)}</div>
                <p>Users with escrow activity</p>
            </div>
            
            <div class="metric">
                <h3>💵 Avg Transaction Size</h3>
                <div class="value">${user_behavior.get("average_transaction_size", 0):,.2f}</div>
                <p>Per escrow amount</p>
            </div>
            
            <div class="metric">
                <h3>🔄 Returning Users</h3>
                <div class="value">{user_behavior.get("returning_users", 0)}</div>
                <p>Yesterday → Today retention</p>
            </div>"""
    
    # Add popular currencies section
    popular_currencies = user_behavior.get("popular_currencies", [])
    if popular_currencies:
        top_currency = popular_currencies[0]
        html_content += f"""
            <div class="metric">
                <h3>🏆 Top Currency</h3>
                <div class="value">{top_currency.get("currency", "N/A")}</div>
                <p>{top_currency.get("count", 0)} transactions</p>
            </div>"""
    else:
        html_content += """
            <div class="metric">
                <h3>🏆 Top Currency</h3>
                <div class="value">N/A</div>
                <p>No transactions today</p>
            </div>"""
    
    html_content += """
        </div>
        
        <div class="section">
            <h2>⚙️ Operational Efficiency</h2>"""
    
    # Add Operational Efficiency section
    operational = data.get("operational_metrics", {})
    
    html_content += f"""
            <div class="metric">
                <h3>⏱️ Avg Completion Time</h3>
                <div class="value">{operational.get("avg_completion_time_hours", 0):.1f}h</div>
                <p>Escrow lifecycle duration</p>
            </div>
            
            <div class="metric">
                <h3>🤖 Auto-Cashout Success</h3>
                <div class="value">{operational.get("auto_cashout_success_rate", 0)}%</div>
                <p>Automated vs manual cashouts</p>
            </div>
            
            <div class="metric">
                <h3>👨‍💼 Admin Interventions</h3>
                <div class="value">{operational.get("admin_interventions", 0)}</div>
                <p>Manual cashout approvals</p>
            </div>
            
            <div class="metric">
                <h3>📋 Pending Actions</h3>
                <div class="value">{operational.get("pending_admin_actions", 0)}</div>
                <p>Items requiring attention</p>
            </div>
        </div>
        
        <div class="section">
            <h2>💰 Financial Risk Management</h2>"""
    
    # Add Financial Risk Management section
    risk_mgmt = data.get("risk_management", {})
    large_pending = risk_mgmt.get("large_pending_escrows", {})
    
    html_content += f"""
            <div class="metric">
                <h3>💸 Large Pending Escrows</h3>
                <div class="value">{large_pending.get("count", 0)}</div>
                <p>${large_pending.get("total_value_usd", 0):,.0f} total value</p>
            </div>
            
            <div class="metric">
                <h3>🚫 Stuck Transactions</h3>
                <div class="value">{risk_mgmt.get("stuck_transactions", 0)}</div>
                <p>Pending >24 hours</p>
            </div>
            
            <div class="metric">
                <h3>🌍 Currency Exposure</h3>
                <div class="value">{len(risk_mgmt.get("currency_exposure", []))}</div>
                <p>Active currencies</p>
            </div>
            
            <div class="metric">
                <h3>⚖️ Overall Risk Score</h3>
                <div class="value">{risk_mgmt.get("overall_risk_score", 0)}/100</div>
                <p>Financial risk level</p>
            </div>
        </div>
        
        <div class="section">
            <h2>📈 GROWTH METRICS</h2>"""
    
    # Add Growth Metrics section
    html_content += f"""
            <div class="metric">
                <h3>👥 Active Users Today</h3>
                <div class="value">{growth_metrics.get("active_users_today", 0)}</div>
                <p>Users with escrow activity</p>
            </div>
            
            <div class="metric">
                <h3>📊 User Retention Rate</h3>
                <div class="value">{growth_metrics.get("retention_rate", 0)}%</div>
                <p>Users active 7 days ago still active today</p>
            </div>
            
            <div class="metric">
                <h3>📉 Churn Rate</h3>
                <div class="value">{growth_metrics.get("churn_rate", 0)}%</div>
                <p>Users who stopped using the platform</p>
            </div>
            
            <div class="metric">
                <h3>📈 Week-over-Week Growth</h3>
                <div class="value" style="color: {'#28a745' if growth_metrics.get('wow_growth', 0) > 0 else '#dc3545'};">{growth_metrics.get("wow_growth", 0):+.2f}%</div>
                <p>Active user growth vs last week</p>
            </div>
            
            <div class="metric">
                <h3>🆕 New Users This Week</h3>
                <div class="value">{growth_metrics.get("new_users_this_week", 0)}</div>
                <p>Total registered users: {growth_metrics.get("total_users", 0)}</p>
            </div>
        </div>
        
        <div class="section">
            <h2>🆘 CUSTOMER SUPPORT</h2>"""
    
    # Add Customer Support section
    html_content += f"""
            <div class="metric">
                <h3>🔴 Active Disputes</h3>
                <div class="value" style="color: {'#dc3545' if support_metrics.get('active_disputes', 0) > 0 else '#28a745'};">{support_metrics.get("active_disputes", 0)}</div>
                <p>Currently under review</p>
            </div>
            
            <div class="metric">
                <h3>📥 Disputes Opened Today</h3>
                <div class="value">{support_metrics.get("disputes_opened_today", 0)}</div>
                <p>New disputes filed</p>
            </div>
            
            <div class="metric">
                <h3>✅ Disputes Resolved Today</h3>
                <div class="value" style="color: #28a745;">{support_metrics.get("disputes_resolved_today", 0)}</div>
                <p>Successfully closed</p>
            </div>
            
            <div class="metric">
                <h3>⏱️ Avg Response Time</h3>
                <div class="value">{support_metrics.get("avg_response_time_hours", 0):.1f}h</div>
                <p>Time to resolve disputes</p>
            </div>
            
            <div class="metric">
                <h3>📋 Pending Support Actions</h3>
                <div class="value" style="color: {'#dc3545' if support_metrics.get('pending_actions', 0) > 5 else '#28a745'};">{support_metrics.get("pending_actions", 0)}</div>
                <p>Items requiring attention</p>
            </div>
        </div>
        
        <div class="section">
            <h2>⚡ PLATFORM UPTIME</h2>"""
    
    # Add Platform Uptime section
    html_content += f"""
            <div class="metric">
                <h3>✅ System Availability</h3>
                <div class="value" style="color: {'#28a745' if platform_uptime.get('availability', 0) >= 99 else '#ffc107' if platform_uptime.get('availability', 0) >= 95 else '#dc3545'};">{platform_uptime.get("availability", 0):.2f}%</div>
                <p>Platform uptime</p>
            </div>
            
            <div class="metric">
                <h3>✅ Success Rate</h3>
                <div class="value" style="color: #28a745;">{platform_uptime.get("success_rate", 0):.2f}%</div>
                <p>{platform_uptime.get("successful_transactions", 0)} of {platform_uptime.get("total_transactions", 0)} transactions</p>
            </div>
            
            <div class="metric">
                <h3>❌ Error Rate</h3>
                <div class="value" style="color: {'#dc3545' if platform_uptime.get('error_rate', 0) > 5 else '#28a745'};">{platform_uptime.get("error_rate", 0):.2f}%</div>
                <p>{platform_uptime.get("failed_transactions", 0)} failed transactions</p>
            </div>
            
            <div class="metric">
                <h3>⚡ Avg Response Time</h3>
                <div class="value">{platform_uptime.get("avg_response_time_seconds", 0):.2f}s</div>
                <p>API response time</p>
            </div>
        </div>
        
        <div class="footer">
            <p><strong>Report Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            <p><strong>Platform:</strong> {Config.PLATFORM_NAME}</p>
            <p><em>This is an automated daily financial report. For detailed analytics, check the admin panel.</em></p>
        </div>
    </body>
    </html>
    """

    return html_content

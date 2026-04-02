def main() -> None:
    log.info("=" * 60)
    log.info("  Notification Bot starting up")
    log.info("=" * 60)

    _validate_config()
    init_db()

    # ✅ use existing scheduler (DON’T redefine)

    scheduler.add_job(
        check_youtube_channels,
        trigger=IntervalTrigger(minutes=YOUTUBE_POLL_INTERVAL_MINUTES),
        id="youtube",
        next_run_time=None,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        check_twitter,
        trigger=IntervalTrigger(minutes=TWITTER_POLL_INTERVAL_MINUTES),
        id="twitter",
        next_run_time=None,
        misfire_grace_time=60,
    )

    log.info(
        "Scheduler started. YouTube every %d min | Twitter every %d min",
        YOUTUBE_POLL_INTERVAL_MINUTES,
        TWITTER_POLL_INTERVAL_MINUTES,
    )

    time.sleep(3)
    log.info("Running initial checks...")
    check_youtube_channels()
    check_twitter()

    scheduler.start()
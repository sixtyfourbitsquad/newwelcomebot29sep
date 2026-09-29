package com.welcomebot.platform;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component @Profile("worker")
public class Operations {
    private final Jobs jobs;
    private final MeterRegistry metrics;
    private final ConcurrentHashMap<String,AtomicLong> counts=new ConcurrentHashMap<>();
    private final AtomicLong age=new AtomicLong();
    public Operations(Jobs jobs,MeterRegistry metrics) {
        this.jobs=jobs; this.metrics=metrics;
        metrics.gauge("automation_oldest_pending_seconds",age);
    }
    @Scheduled(fixedDelay=30000)
    public void measure() {
        try {
            var rows=jobs.db.queryForList("SELECT state,count(*) n FROM jobs GROUP BY state");
            counts.values().forEach(v->v.set(0));
            for(var r:rows) counts.computeIfAbsent((String)r.get("state"),s->{
                var v=new AtomicLong(); metrics.gauge("automation_job_count",java.util.List.of(io.micrometer.core.instrument.Tag.of("state",s)),v); return v;
            }).set(((Number)r.get("n")).longValue());
            age.set(jobs.db.queryForObject("SELECT coalesce(greatest(0,extract(epoch FROM now()-min(due_at))),0)::bigint FROM jobs WHERE state='PENDING'",Long.class));
        } catch(Exception e) { org.slf4j.LoggerFactory.getLogger(Operations.class).warn("operational_metrics_failed category={}",e.getClass().getSimpleName()); }
    }
    @Scheduled(fixedDelay=3600000,initialDelay=60000)
    public void cleanup() {
        // Keep dedupe tombstones; remove personal content in small batches after the retention window.
        try {
            jobs.db.update("UPDATE update_inbox SET payload='{}'::jsonb WHERE (bot_key,update_id) IN (SELECT bot_key,update_id FROM update_inbox WHERE received_at<now()-interval '7 days' AND state='PROCESSED' AND payload<>'{}'::jsonb LIMIT 1000)");
            jobs.db.update("UPDATE jobs SET payload='{}'::jsonb WHERE id IN (SELECT id FROM jobs WHERE created_at<now()-interval '30 days' AND state='DONE' AND payload<>'{}'::jsonb LIMIT 1000)");
        } catch(Exception e) { org.slf4j.LoggerFactory.getLogger(Operations.class).warn("cleanup_failed category={}",e.getClass().getSimpleName()); }
    }
}

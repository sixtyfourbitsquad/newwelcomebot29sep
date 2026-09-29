package com.welcomebot.platform;

import com.rabbitmq.client.Channel;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.scheduling.annotation.Scheduled;
import org.slf4j.LoggerFactory;
import io.micrometer.core.instrument.MeterRegistry;

@Component @Profile({"worker","broadcast"})
public class Worker {
    private final Jobs jobs;
    private final Telegram telegram;
    private final Automation automation;
    private final RabbitTemplate rabbit;
    private final MeterRegistry metrics;
    private final boolean bulk;
    public Worker(Jobs jobs,Telegram telegram,Automation automation,RabbitTemplate rabbit,MeterRegistry metrics,org.springframework.core.env.Environment env) {
        this.jobs=jobs; this.telegram=telegram; this.automation=automation; this.rabbit=rabbit; this.metrics=metrics;
        bulk=env.matchesProfiles("broadcast");
    }
    @Scheduled(fixedDelay=1000)
    public void dispatch() {
        try {
            // A stale delivery lease is ambiguous; domain work is idempotent and can be retried.
            jobs.db.update("UPDATE jobs SET state=CASE WHEN kind IN ('join','broadcast') THEN 'PENDING' ELSE 'UNKNOWN' END,lease_token=null,lease_until=null,last_error='lease_expired' WHERE state='RUNNING' AND lease_until<now()");
            String predicate=bulk?"kind IN ('broadcast','delivery.low')":"kind NOT IN ('broadcast','delivery.low')";
            var rows=jobs.db.queryForList("SELECT id,kind FROM jobs WHERE state='PENDING' AND due_at<=now() AND signal_after<=now() AND "+predicate+" ORDER BY due_at LIMIT 40");
            for(var row:rows) {
                var props=new MessageProperties(); props.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
                var correlation=new CorrelationData(UUID.randomUUID().toString());
                rabbit.send("automation.work",(String)row.get("kind"),new Message(row.get("id").toString().getBytes(java.nio.charset.StandardCharsets.UTF_8),props),correlation);
                var confirm=correlation.getFuture().get(3,TimeUnit.SECONDS);
                if(!confirm.isAck() || correlation.getReturned()!=null) throw new IllegalStateException("publish_rejected");
                // If delivery or commit races this update, it cannot overwrite a running job.
                jobs.db.update("UPDATE jobs SET signal_after=now()+interval '30 seconds' WHERE id=? AND state='PENDING'",row.get("id"));
            }
        } catch(Exception e) { LoggerFactory.getLogger(Worker.class).warn("job_dispatch_failed category={}",e.getClass().getSimpleName()); }
    }
    @RabbitListener(queues="#{'${WORKER_QUEUES:automation.join,automation.welcome,automation.live,automation.delivery.high,automation.delivery.normal}'.split(',')}")
    public void consume(Message message,Channel channel) throws java.io.IOException {
        long tag=message.getMessageProperties().getDeliveryTag();
        UUID id;
        try { id=UUID.fromString(new String(message.getBody(),java.nio.charset.StandardCharsets.UTF_8)); }
        catch(Exception e) { channel.basicReject(tag,false); return; }
        UUID token=UUID.randomUUID();
        try {
            var rows=jobs.db.queryForList("UPDATE jobs SET state='RUNNING',lease_token=?,lease_until=now()+interval '90 seconds',attempts=attempts+1 WHERE id=? AND state='PENDING' AND due_at<=now() RETURNING *",token,id);
            if(rows.isEmpty()) { channel.basicAck(tag,false); return; }
            var job=rows.getFirst();
            var payload=jobs.parse(job.get("payload").toString());
            String kind=(String)job.get("kind");
            boolean sent=false;
            try {
                if(kind.equals("join")) automation.update(payload);
                else if(kind.equals("broadcast")) automation.fanout(UUID.fromString(payload.path("campaign").asText()));
                else {
                    boolean skip=payload.has("expires") && payload.path("expires").asLong()<java.time.Instant.now().getEpochSecond();
                    if(payload.has("campaign")) {
                        String state=jobs.db.queryForObject("SELECT state FROM campaigns WHERE id=?",String.class,UUID.fromString(payload.path("campaign").asText()));
                        if("PAUSED".equals(state)) throw new Telegram.Retry(30);
                        skip|="CANCELLED".equals(state);
                        skip|=!Boolean.TRUE.equals(jobs.db.queryForObject("SELECT active FROM users WHERE id=?",Boolean.class,payload.path("chat_id").asLong()));
                    }
                    if(!skip) { telegram.send(payload,kind); sent=true; }
                }
                finish(id,token,"DONE",null,0);
            } catch(Telegram.Retry e) { finish(id,token,"PENDING","rate_or_pause",e.seconds); }
              catch(Telegram.Unknown e) { finish(id,token,"UNKNOWN","ambiguous_telegram_result",0); }
              catch(Telegram.Permanent e) {
                  if(e.code==403) jobs.db.update("UPDATE users SET active=false WHERE id=?",payload.path("chat_id").asLong());
                  finish(id,token,"DEAD","telegram_"+e.code,0);
              } catch(Exception e) {
                  int attempts=((Number)job.get("attempts")).intValue();
                  finish(id,token,sent?"UNKNOWN":attempts>=8?"DEAD":"PENDING",sent?"sent_but_commit_failed":e.getClass().getSimpleName(),Math.min(3600,1<<Math.min(attempts,11)));
              }
            channel.basicAck(tag,false);
        } catch(Exception e) {
            // PostgreSQL remains authoritative; uncommitted work is recovered by the lease sweeper.
            LoggerFactory.getLogger(Worker.class).warn("job_execution_failed id={} category={}",id,e.getClass().getSimpleName());
            channel.basicNack(tag,false,false);
        }
    }
    private void finish(UUID id,UUID token,String state,String error,int delay) {
        jobs.db.update("UPDATE jobs SET state=?,last_error=?,due_at=now()+?*interval '1 second',lease_token=null,lease_until=null WHERE id=? AND lease_token=?",state,error,delay,id,token);
        metrics.counter("automation_jobs","state",state).increment();
        if(state.equals("UNKNOWN")||state.equals("DEAD")) LoggerFactory.getLogger(Worker.class).warn("job_needs_review id={} state={} category={}",id,state,error);
    }
}

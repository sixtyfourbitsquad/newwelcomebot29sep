package com.welcomebot.platform;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class Automation {
    private final Jobs jobs;
    public Automation(Jobs jobs) { this.jobs=jobs; }
    @Transactional
    public void update(JsonNode u) {
        long id=u.path("update_id").asLong();
        JsonNode m=u.path("message");
        if(m.path("chat").path("type").asText().equals("private")) {
            long chat=m.path("chat").path("id").asLong();
            String text=m.path("text").asText();
            if(text.equals("/start") || text.startsWith("/start ")) {
                jobs.db.update("INSERT INTO users(id) VALUES (?) ON CONFLICT(id) DO UPDATE SET active=true",chat);
                jobs.send("start:"+id,chat,"Welcome! You are subscribed. Use /stop to unsubscribe.","delivery.high",0);
            } else if(text.equals("/stop")) {
                jobs.db.update("UPDATE users SET active=false WHERE id=?",chat);
                jobs.send("stop:"+id,chat,"You are unsubscribed.","delivery.high",0);
            }
        }
        JsonNode join=u.path("chat_join_request");
        if(!join.isMissingNode()) {
            long channel=join.path("chat").path("id").asLong();
            var channels=jobs.db.queryForList("SELECT * FROM channels WHERE id=?",channel);
            if(!channels.isEmpty()) {
                var c=channels.getFirst();
                jobs.db.update("INSERT INTO join_requests(update_id,channel_id,user_id) VALUES (?,?,?) ON CONFLICT DO NOTHING",id,channel,join.path("from").path("id").asLong());
                // Approval takes precedence: Telegram may revoke the temporary DM permission on approval.
                if(Boolean.TRUE.equals(c.get("auto_approve"))) {
                    jobs.add("approve:"+id,"delivery.high",Map.of("method","approveChatJoinRequest","chat_id",channel,"user_id",join.path("from").path("id").asLong()),0);
                } else {
                    long chat=join.path("user_chat_id").asLong();
                    long expires=join.path("date").asLong()+290;
                    var steps=jobs.db.queryForList("SELECT * FROM welcome_steps WHERE channel_id=? ORDER BY position",channel);
                    if(steps.isEmpty()) steps=List.of(Map.of("position",0,"delay_seconds",0,"text",c.get("welcome")));
                    for(var s:steps) jobs.add("welcome:"+id+":"+s.get("position"),"welcome",Map.of("method","sendMessage","chat_id",chat,"text",s.get("text"),"expires",expires),((Number)s.get("delay_seconds")).intValue());
                }
            }
        }
        if(m.has("video_chat_started")) {
            long channel=m.path("chat").path("id").asLong();
            var texts=jobs.db.queryForList("SELECT live_text FROM channels WHERE id=? AND live_text IS NOT NULL",channel);
            if(!texts.isEmpty()) {
                jobs.db.update("INSERT INTO live_events(update_id,channel_id) VALUES (?,?) ON CONFLICT DO NOTHING",id,channel);
                jobs.send("live:"+id,channel,(String)texts.getFirst().get("live_text"),"live",0);
            }
        }
        jobs.db.update("UPDATE update_inbox SET state='PROCESSED' WHERE bot_key='default' AND update_id=?",id);
    }
    @Transactional
    public void fanout(UUID campaign) {
        var rows=jobs.db.queryForList("SELECT * FROM campaigns WHERE id=? FOR UPDATE",campaign);
        if(rows.isEmpty()) return;
        var c=rows.getFirst();
        if(!c.get("state").equals("RUNNING")) return;
        if(jobs.db.queryForObject("SELECT count(*) FROM (SELECT 1 FROM jobs WHERE kind='delivery.low' AND state IN ('PENDING','RUNNING') LIMIT 5000) backlog",Integer.class)>=5000) throw new Telegram.Retry(5);
        var recipients=jobs.db.queryForList("SELECT id FROM users WHERE active AND id>? AND created_at<=? ORDER BY id LIMIT 200",c.get("cursor"),c.get("cutoff"));
        long cursor=((Number)c.get("cursor")).longValue();
        for(var r:recipients) {
            cursor=((Number)r.get("id")).longValue();
            jobs.add("campaign:"+campaign+":"+cursor,"delivery.low",Map.of("method","sendMessage","chat_id",cursor,"text",c.get("text"),"campaign",campaign.toString()),0);
        }
        jobs.db.update("UPDATE campaigns SET cursor=?,state=? WHERE id=?",cursor,recipients.size()<200?"COMPLETED":"RUNNING",campaign);
        if(recipients.size()==200) jobs.add("fanout:"+campaign+":"+cursor,"broadcast",Map.of("campaign",campaign.toString()),1);
    }
}

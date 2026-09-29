package com.welcomebot.platform;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.http.*;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

@RestController @Profile("api")
public class Webhook {
    private final Jobs jobs;
    private final String secret;
    public Webhook(Jobs jobs,@Value("${TELEGRAM_WEBHOOK_SECRET}") String secret) {
        if(!secret.matches("[A-Za-z0-9_-]{32,256}")) throw new IllegalArgumentException("Webhook secret must be 32-256 URL-safe characters");
        this.jobs=jobs; this.secret=secret;
    }
    static boolean same(String a,String b) { return a!=null && b!=null && MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8),b.getBytes(StandardCharsets.UTF_8)); }
    @PostMapping("/telegram/webhook/{path}") @Transactional
    public void accept(@PathVariable String path,HttpServletRequest request) throws java.io.IOException {
        if(!same(secret,path)||!same(secret,request.getHeader("X-Telegram-Bot-Api-Secret-Token"))) throw new ResponseStatusException(HttpStatus.FORBIDDEN);
        byte[] body=request.getInputStream().readNBytes(262145);
        if(body.length>262144) throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE);
        com.fasterxml.jackson.databind.JsonNode update;
        try { update=jobs.parse(new String(body,StandardCharsets.UTF_8)); } catch(Exception e) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST); }
        if(update==null || !update.isObject() || !update.path("update_id").isIntegralNumber() || !update.path("update_id").canConvertToLong() || update.path("update_id").asLong()<0) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        long id=update.path("update_id").asLong();
        if(jobs.db.update("INSERT INTO update_inbox(bot_key,update_id,payload) VALUES ('default',?,?::jsonb) ON CONFLICT DO NOTHING",id,update.toString())==1)
            jobs.add("update:"+id,"join",update,0);
    }
}

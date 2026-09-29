package com.welcomebot.platform;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.time.Duration;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import io.github.resilience4j.circuitbreaker.*;
import io.micrometer.core.instrument.MeterRegistry;

@Service
public class Telegram {
    public static class Retry extends RuntimeException { final int seconds; Retry(int seconds) { this.seconds=seconds; } }
    public static class Permanent extends RuntimeException { final int code; Permanent(int code) { this.code=code; } }
    public static class Unknown extends RuntimeException {}
    private final WebClient client;
    private final StringRedisTemplate redis;
    private final MeterRegistry metrics;
    private final CircuitBreaker breaker=CircuitBreaker.ofDefaults("telegram");
    private final DefaultRedisScript<Long> limit=new DefaultRedisScript<>("""
        if redis.call('exists',KEYS[4])==1 then return 0 end
        local n=tonumber(redis.call('get',KEYS[1]) or '0')
        local low=tonumber(redis.call('get',KEYS[3]) or '0')
        if n>=25 or redis.call('exists',KEYS[2])==1 or (ARGV[1]=='low' and low>=20) then return 0 end
        redis.call('incr',KEYS[1]); if n==0 then redis.call('pexpire',KEYS[1],1000) end
        redis.call('set',KEYS[2],'1','PX',ARGV[2])
        if ARGV[1]=='low' then redis.call('incr',KEYS[3]); if low==0 then redis.call('pexpire',KEYS[3],1000) end end
        return 1
        """,Long.class);
    public Telegram(@Value("${TELEGRAM_BOT_TOKEN}") String token,StringRedisTemplate redis,MeterRegistry metrics) {
        this.redis=redis; this.metrics=metrics;
        var http=reactor.netty.http.client.HttpClient.create().option(io.netty.channel.ChannelOption.CONNECT_TIMEOUT_MILLIS,3000).responseTimeout(Duration.ofSeconds(10));
        client=WebClient.builder().baseUrl("https://api.telegram.org/bot"+token).clientConnector(new org.springframework.http.client.reactive.ReactorClientHttpConnector(http)).codecs(c->c.defaultCodecs().maxInMemorySize(262144)).build();
    }
    public void send(JsonNode payload,String lane) {
        long chat=payload.path("chat_id").asLong();
        Long allowed;
        try { allowed=redis.execute(limit,List.of("{telegram}:global","{telegram}:chat:"+chat,"{telegram}:bulk","{telegram}:cooldown"),lane.equals("delivery.low")?"low":"high",chat<0?"3100":"1100"); }
        catch(org.springframework.dao.DataAccessException e) { throw new Retry(5); }
        if(!Long.valueOf(1).equals(allowed)) throw new Retry(1);
        ObjectNode body=((ObjectNode)payload).deepCopy();
        String method=body.remove("method").asText();
        body.remove(List.of("campaign","expires"));
        if(!breaker.tryAcquirePermission()) throw new Retry(30);
        long start=System.nanoTime();
        JsonNode result;
        try {
            result=client.post().uri("/"+method).bodyValue(body).exchangeToMono(r->r.bodyToMono(JsonNode.class)).block(Duration.ofSeconds(15));
        } catch(Exception e) {
            breaker.onError(System.nanoTime()-start,java.util.concurrent.TimeUnit.NANOSECONDS,new RuntimeException("telegram_transport"));
            metrics.counter("telegram_failures","category","unknown").increment();
            throw new Unknown(); // Do not blindly retry a request which Telegram might already have accepted.
        }
        breaker.onSuccess(System.nanoTime()-start,java.util.concurrent.TimeUnit.NANOSECONDS);
        if(result==null || !result.has("ok")) throw new Unknown();
        if(result.path("ok").asBoolean()) { metrics.counter("telegram_deliveries").increment(); return; }
        int code=result.path("error_code").asInt();
        metrics.counter("telegram_failures","category",code==429?"rate_limit":"rejected").increment();
        if(code==429) {
            int seconds=Math.max(1,result.path("parameters").path("retry_after").asInt(30));
            try { redis.opsForValue().set("{telegram}:cooldown","1",Duration.ofSeconds(seconds)); }
            catch(org.springframework.dao.DataAccessException e) { /* This job still respects retry_after; other sends fail closed while Redis is unavailable. */ }
            throw new Retry(seconds);
        }
        if(code>=500) throw new Unknown();
        throw new Permanent(code);
    }
}

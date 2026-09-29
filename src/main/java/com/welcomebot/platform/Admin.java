package com.welcomebot.platform;

import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

@RestController @Profile("api") @RequestMapping("/admin")
public class Admin {
    private final Jobs jobs;
    private final String key;
    public Admin(Jobs jobs,@Value("${ADMIN_API_KEY}") String key) {
        if(key.length()<32) throw new IllegalArgumentException("Admin key must contain at least 32 characters");
        this.jobs=jobs; this.key=key;
    }
    private void auth(HttpServletRequest request) {
        if(!Webhook.same("Bearer "+key,request.getHeader("Authorization"))) throw new ResponseStatusException(HttpStatus.UNAUTHORIZED);
    }
    private void audit(String action) { jobs.db.update("INSERT INTO audit_logs(action) VALUES (?)",action); }
    public record ChannelConfig(long id,String title,boolean autoApprove,String welcome,String liveText) {}
    public record Step(int position,int delaySeconds,String text) {}
    public record Broadcast(String text,int delaySeconds) {}
    private static void validText(String text) { if(text==null||text.isBlank()||text.length()>4096) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"Text must have 1-4096 characters"); }
    @PutMapping("/channels") @Transactional
    public void channel(HttpServletRequest request,@RequestBody ChannelConfig config) {
        auth(request); validText(config.welcome());
        if(config.id()>=0 || config.title()==null || config.title().length()>255) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        if(config.liveText()!=null) validText(config.liveText());
        jobs.db.update("INSERT INTO channels(id,title,auto_approve,welcome,live_text) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,auto_approve=excluded.auto_approve,welcome=excluded.welcome,live_text=excluded.live_text",config.id(),config.title(),config.autoApprove(),config.welcome(),config.liveText());
        audit("channel_configured:"+config.id());
    }
    @PutMapping("/channels/{id}/welcome") @Transactional
    public void welcome(HttpServletRequest request,@PathVariable long id,@RequestBody List<Step> steps) {
        auth(request);
        if(steps.size()>10) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        jobs.db.update("DELETE FROM welcome_steps WHERE channel_id=?",id);
        for(Step s:steps) {
            validText(s.text());
            if(s.delaySeconds()<0||s.delaySeconds()>240) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
            jobs.db.update("INSERT INTO welcome_steps(channel_id,position,delay_seconds,text) VALUES (?,?,?,?)",id,s.position(),s.delaySeconds(),s.text());
        }
        audit("welcome_configured:"+id);
    }
    @PostMapping("/broadcasts") @Transactional
    public Map<String,Object> broadcast(HttpServletRequest request,@RequestBody Broadcast input) {
        auth(request); validText(input.text());
        if(input.delaySeconds()<0||input.delaySeconds()>31536000) throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        UUID id=UUID.randomUUID();
        jobs.db.update("INSERT INTO campaigns(id,text) VALUES (?,?)",id,input.text());
        jobs.add("fanout:"+id+":0","broadcast",Map.of("campaign",id),input.delaySeconds());
        audit("broadcast_created:"+id); return Map.of("id",id);
    }
    @PostMapping("/broadcasts/{id}/{action}") @Transactional
    public void control(HttpServletRequest request,@PathVariable UUID id,@PathVariable String action) {
        auth(request);
        String state=switch(action) { case "pause"->"PAUSED"; case "resume"->"RUNNING"; case "cancel"->"CANCELLED"; default->throw new ResponseStatusException(HttpStatus.BAD_REQUEST); };
        if(jobs.db.update("UPDATE campaigns SET state=? WHERE id=? AND state<>'CANCELLED'",state,id)==0) throw new ResponseStatusException(HttpStatus.CONFLICT);
        if(action.equals("resume")) jobs.add("resume:"+UUID.randomUUID(),"broadcast",Map.of("campaign",id),0);
        audit("broadcast_"+action+":"+id);
    }
    @GetMapping("/status")
    public Map<String,Object> status(HttpServletRequest request) {
        auth(request); return Map.of("jobs",jobs.db.queryForList("SELECT kind,state,count(*) FROM jobs GROUP BY kind,state"),"campaigns",jobs.db.queryForList("SELECT id,state,cursor FROM campaigns ORDER BY cutoff DESC LIMIT 50"));
    }
    @GetMapping("/jobs")
    public List<Map<String,Object>> jobs(HttpServletRequest request,@RequestParam(defaultValue="UNKNOWN") String state) {
        auth(request); return jobs.db.queryForList("SELECT id,kind,state,last_error,created_at FROM jobs WHERE state=? ORDER BY created_at DESC LIMIT 100",state);
    }
}

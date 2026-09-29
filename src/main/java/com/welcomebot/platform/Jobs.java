package com.welcomebot.platform;

import com.fasterxml.jackson.databind.*;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class Jobs {
    final JdbcTemplate db;
    final ObjectMapper json;
    public Jobs(JdbcTemplate db, ObjectMapper json) { this.db=db; this.json=json; }
    public void add(String key, String kind, Object payload, int delay) {
        db.update("INSERT INTO jobs(id,dedupe,kind,payload,due_at) VALUES (?,?,?,?::jsonb,now()+?*interval '1 second') ON CONFLICT(dedupe) DO NOTHING", UUID.randomUUID(),key,kind,encode(payload),delay);
    }
    public String encode(Object value) { try { return json.writeValueAsString(value); } catch(Exception e) { throw new IllegalArgumentException("Invalid payload"); } }
    public JsonNode parse(String value) { try { return json.readTree(value); } catch(Exception e) { throw new IllegalArgumentException("Invalid JSON"); } }
    public void send(String key,long chat,String text,String lane,int delay) { add(key,lane,Map.of("method","sendMessage","chat_id",chat,"text",text),delay); }
}

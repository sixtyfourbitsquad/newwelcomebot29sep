package com.welcomebot.platform;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

@Testcontainers
class DurabilityIT {
    @Container static PostgreSQLContainer<?> postgres=new PostgreSQLContainer<>("postgres:17-alpine");
    JdbcTemplate db;
    Jobs jobs;
    TransactionTemplate transaction;
    @BeforeEach void setup() {
        var source=new DriverManagerDataSource(postgres.getJdbcUrl(),postgres.getUsername(),postgres.getPassword());
        Flyway.configure().dataSource(source).load().migrate();
        db=new JdbcTemplate(source); jobs=new Jobs(db,new ObjectMapper());
        transaction=new TransactionTemplate(new DataSourceTransactionManager(source));
        db.execute("TRUNCATE jobs,campaigns,users,update_inbox CASCADE");
    }
    @Test void failedTransactionCannotLeaveWork() {
        assertThrows(RuntimeException.class,()->transaction.execute(s->{
            jobs.add("rollback","join",Map.of("update_id",1),0);
            throw new RuntimeException("crash");
        }));
        assertEquals(0,db.queryForObject("SELECT count(*) FROM jobs",Integer.class));
    }
    @Test void duplicateWorkAndRestartPreserveSchedule() {
        jobs.add("same","welcome",Map.of("text","hello"),120);
        new Jobs(db,new ObjectMapper()).add("same","welcome",Map.of("text","hello"),120);
        assertEquals(1,db.queryForObject("SELECT count(*) FROM jobs",Integer.class));
        assertEquals(0,db.queryForObject("SELECT count(*) FROM jobs WHERE due_at<=now()",Integer.class));
    }
    @Test void fanoutIsBoundedAndResumesWithoutDuplicateRecipients() {
        db.update("INSERT INTO users(id) SELECT generate_series(1,501)");
        UUID id=UUID.randomUUID();
        db.update("INSERT INTO campaigns(id,text) VALUES (?,?)",id,"hello");
        Automation automation=new Automation(jobs);
        transaction.execute(s->{ automation.fanout(id); return null; });
        assertEquals(200,db.queryForObject("SELECT count(*) FROM jobs WHERE kind='delivery.low'",Integer.class));
        transaction.execute(s->{ automation.fanout(id); return null; });
        transaction.execute(s->{ automation.fanout(id); return null; });
        transaction.execute(s->{ automation.fanout(id); return null; });
        assertEquals(501,db.queryForObject("SELECT count(*) FROM jobs WHERE kind='delivery.low'",Integer.class));
        assertEquals("COMPLETED",db.queryForObject("SELECT state FROM campaigns WHERE id=?",String.class,id));
    }
}

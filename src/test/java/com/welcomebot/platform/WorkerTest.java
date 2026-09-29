package com.welcomebot.platform;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.core.env.Environment;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class WorkerTest {
    @Test void successfulSendWithFailedCommitCannotBecomeRetryable() throws Exception {
        JdbcTemplate db=mock(JdbcTemplate.class);
        UUID id=UUID.randomUUID();
        when(db.queryForList(startsWith("UPDATE jobs SET state='RUNNING'"),any(UUID.class),eq(id)))
            .thenReturn(List.of(Map.of("payload","{\"method\":\"sendMessage\",\"chat_id\":123,\"text\":\"Hi\"}","kind","delivery.high","attempts",1)));
        when(db.update(startsWith("UPDATE jobs SET state=?"),eq("DONE"),isNull(),eq(0),eq(id),any(UUID.class)))
            .thenThrow(new DataAccessResourceFailureException("simulated commit failure"));
        Telegram telegram=mock(Telegram.class);
        var worker=new Worker(new Jobs(db,new ObjectMapper()),telegram,mock(Automation.class),mock(RabbitTemplate.class),new SimpleMeterRegistry(),mock(Environment.class));
        var props=new MessageProperties(); props.setDeliveryTag(1);
        Channel channel=mock(Channel.class);
        worker.consume(new Message(id.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8),props),channel);
        verify(telegram,times(1)).send(any(),eq("delivery.high"));
        verify(db).update(startsWith("UPDATE jobs SET state=?"),eq("UNKNOWN"),eq("sent_but_commit_failed"),eq(2),eq(id),any(UUID.class));
        verify(channel).basicAck(1,false);
    }
    @Test void malformedQueueSignalIsDeadLettered() throws Exception {
        JdbcTemplate db=mock(JdbcTemplate.class);
        var worker=new Worker(new Jobs(db,new ObjectMapper()),mock(Telegram.class),mock(Automation.class),mock(RabbitTemplate.class),new SimpleMeterRegistry(),mock(Environment.class));
        var props=new MessageProperties(); props.setDeliveryTag(7);
        Channel channel=mock(Channel.class);
        worker.consume(new Message(new byte[]{1,2},props),channel);
        verify(channel).basicReject(7,false);
        verifyNoInteractions(db);
    }
}

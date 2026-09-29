package com.welcomebot.platform.queue;

import com.welcomebot.platform.config.QueueProperties;
import java.util.ArrayList;
import java.util.List;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
public class QueueTopology {
    public static final String WORK_EXCHANGE = "automation.work";
    public static final String DEAD_EXCHANGE = "automation.dead";
    public static final List<String> ROUTES = List.of(
            "join", "welcome", "delivery.high", "delivery.normal", "delivery.low", "live", "broadcast", "retry");

    @Bean
    public Declarables automationTopology(QueueProperties settings) {
        var work = new DirectExchange(WORK_EXCHANGE, true, false);
        var dead = new DirectExchange(DEAD_EXCHANGE, true, false);
        List<Declarable> declarations = new ArrayList<>(List.of(work, dead));
        for (String route : ROUTES) {
            Queue queue = bounded("automation." + route, settings)
                    .deadLetterExchange(DEAD_EXCHANGE)
                    .deadLetterRoutingKey(route)
                    .withArgument("x-delivery-limit", settings.deliveryLimit())
                    .withArgument("x-dead-letter-strategy", "at-least-once")
                    .build();
            Queue dlq = bounded("automation." + route + ".dead", settings).build();
            declarations.add(queue);
            declarations.add(dlq);
            declarations.add(BindingBuilder.bind(queue).to(work).with(route));
            declarations.add(BindingBuilder.bind(dlq).to(dead).with(route));
        }
        return new Declarables(declarations);
    }

    private QueueBuilder bounded(String name, QueueProperties settings) {
        return QueueBuilder.durable(name).quorum()
                .withArgument("x-overflow", "reject-publish")
                .withArgument("x-max-length", settings.maxLength())
                .withArgument("x-max-length-bytes", settings.maxLengthBytes());
    }
}

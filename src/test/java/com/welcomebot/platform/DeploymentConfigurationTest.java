package com.welcomebot.platform;

import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;
import static org.junit.jupiter.api.Assertions.*;

class DeploymentConfigurationTest {
    @Test void infrastructurePortsStayPrivate() throws Exception {
        Map<String,Object> compose=new Yaml().load(Files.readString(Path.of("docker-compose.yml")));
        Map<?,?> services=(Map<?,?>)compose.get("services");
        for(String service:List.of("app","worker","broadcast-worker","postgres","redis","rabbitmq")) {
            Map<?,?> config=(Map<?,?>)services.get(service);
            assertFalse(config.containsKey("ports"),service+" must not publish ports");
            assertTrue(config.containsKey("mem_limit"),service+" must have a memory limit");
            assertTrue(config.containsKey("healthcheck"),service+" must have a health check");
        }
    }
    @Test void operationalYamlParses() throws Exception {
        for(String file:List.of("src/main/resources/application.yml","deploy/prometheus.yml","deploy/alerts.yml",".github/workflows/verify.yml"))
            assertInstanceOf(Map.class,new Yaml().load(Files.readString(Path.of(file))),file);
    }
}

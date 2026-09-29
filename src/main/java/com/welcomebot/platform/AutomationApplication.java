package com.welcomebot.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
@org.springframework.scheduling.annotation.EnableScheduling
public class AutomationApplication {
    public static void main(String[] args) {
        SpringApplication.run(AutomationApplication.class, args);
    }
}

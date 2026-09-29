package com.welcomebot.platform.config;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties("platform.queues")
public record QueueProperties(
        @Min(1) @Max(1_000_000) int maxLength,
        @Min(1024) long maxLengthBytes,
        @Min(1) @Max(20) int deliveryLimit) {
}

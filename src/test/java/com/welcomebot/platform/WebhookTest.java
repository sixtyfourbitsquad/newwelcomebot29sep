package com.welcomebot.platform;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.server.ResponseStatusException;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class WebhookTest {
    private final String secret="a".repeat(40);
    @Test void checksBothSecretsBeforeReadingBody() {
        Jobs jobs=mock(Jobs.class);
        var hook=new Webhook(jobs,secret);
        var request=new MockHttpServletRequest();
        request.addHeader("X-Telegram-Bot-Api-Secret-Token",secret);
        assertThrows(ResponseStatusException.class,()->hook.accept("incorrect",request));
        verifyNoInteractions(jobs);
    }
    @Test void rejectsOversizedBody() {
        var hook=new Webhook(mock(Jobs.class),secret);
        var request=new MockHttpServletRequest();
        request.addHeader("X-Telegram-Bot-Api-Secret-Token",secret);
        request.setContent(new byte[262145]);
        var error=assertThrows(ResponseStatusException.class,()->hook.accept(secret,request));
        assertEquals(413,error.getStatusCode().value());
    }
    @Test void secretsHaveNoNullOrPrefixMatch() {
        assertFalse(Webhook.same(secret,null));
        assertFalse(Webhook.same(secret,secret+"b"));
        assertTrue(Webhook.same(secret,secret));
    }
}

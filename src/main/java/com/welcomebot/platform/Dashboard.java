package com.welcomebot.platform;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

@Controller @Profile("api")
public class Dashboard {
    @GetMapping("/admin/dashboard")
    public String dashboard() { return "forward:/admin/dashboard.html"; }
}

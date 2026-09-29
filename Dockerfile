FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -B dependency:go-offline
COPY src src
COPY docker-compose.yml docker-compose.yml
COPY deploy deploy
COPY .github .github
RUN mvn -B package

FROM eclipse-temurin:21-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home bot
WORKDIR /app
COPY --from=build /build/target/automation-platform-*.jar /app/app.jar
USER 10001
ENV JAVA_TOOL_OPTIONS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=65 -XX:InitialRAMPercentage=20 -XX:+ExitOnOutOfMemoryError"
EXPOSE 8080 9091
ENTRYPOINT ["java","-jar","/app/app.jar"]

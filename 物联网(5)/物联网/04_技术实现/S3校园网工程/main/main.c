#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT BIT1

typedef struct {
    char location[512];
    char cookie[256];
} portal_response_t;

static const char *TAG = "focuscube_s3";
static EventGroupHandle_t s_wifi_events;
static int s_retry_count;

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_count < CONFIG_FOCUSCUBE_WIFI_MAXIMUM_RETRY) {
            s_retry_count++;
            ESP_LOGW(TAG, "WiFi disconnected; retry %d/%d", s_retry_count,
                     CONFIG_FOCUSCUBE_WIFI_MAXIMUM_RETRY);
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_wifi_events, WIFI_FAILED_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "BUPT-portal IP: " IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_count = 0;
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    }
}

static bool wifi_connect(void)
{
    s_wifi_events = xEventGroupCreate();
    if (s_wifi_events == NULL) {
        ESP_LOGE(TAG, "Cannot create WiFi event group");
        return false;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               wifi_event_handler, NULL));

    wifi_config_t wifi_config = {0};
    snprintf((char *)wifi_config.sta.ssid, sizeof(wifi_config.sta.ssid), "%s",
             CONFIG_FOCUSCUBE_WIFI_SSID);
    snprintf((char *)wifi_config.sta.password, sizeof(wifi_config.sta.password), "%s",
             CONFIG_FOCUSCUBE_WIFI_PASSWORD);
    wifi_config.sta.threshold.authmode =
        CONFIG_FOCUSCUBE_WIFI_PASSWORD[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Connecting to SSID %s", CONFIG_FOCUSCUBE_WIFI_SSID);
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_events, WIFI_CONNECTED_BIT | WIFI_FAILED_BIT, pdFALSE, pdFALSE,
        pdMS_TO_TICKS(30000));
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

static esp_err_t http_event_handler(esp_http_client_event_t *event)
{
    portal_response_t *response = (portal_response_t *)event->user_data;
    if (response == NULL || event->event_id != HTTP_EVENT_ON_HEADER ||
        event->header_key == NULL || event->header_value == NULL) {
        return ESP_OK;
    }

    if (strcasecmp(event->header_key, "Location") == 0) {
        snprintf(response->location, sizeof(response->location), "%s",
                 event->header_value);
    } else if (strcasecmp(event->header_key, "Set-Cookie") == 0 &&
               response->cookie[0] == '\0') {
        snprintf(response->cookie, sizeof(response->cookie), "%s",
                 event->header_value);
    }
    return ESP_OK;
}

static bool portal_request(const char *url, esp_http_client_method_t method,
                           const char *post_data, const char *cookie,
                           portal_response_t *response, int *status_code)
{
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 8000,
        .event_handler = http_event_handler,
        .user_data = response,
        .buffer_size = 1024,
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return false;
    }

    esp_http_client_set_method(client, method);
    esp_http_client_set_header(client, "User-Agent", "FocusCube-S3 ESP-IDF/6.0.2");
    if (cookie != NULL && cookie[0] != '\0') {
        esp_http_client_set_header(client, "Cookie", cookie);
    }
    if (post_data != NULL) {
        esp_http_client_set_header(client, "Content-Type",
                                   "application/x-www-form-urlencoded");
        esp_http_client_set_post_field(client, post_data, strlen(post_data));
    }

    esp_err_t result = esp_http_client_perform(client);
    *status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    return result == ESP_OK;
}

static bool url_encode(const char *input, char *output, size_t output_size)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t used = 0;

    for (const unsigned char *cursor = (const unsigned char *)input;
         *cursor != '\0'; cursor++) {
        bool plain = (*cursor >= 'a' && *cursor <= 'z') ||
                     (*cursor >= 'A' && *cursor <= 'Z') ||
                     (*cursor >= '0' && *cursor <= '9') || *cursor == '-' ||
                     *cursor == '_' || *cursor == '.' || *cursor == '~';
        size_t needed = plain ? 1 : 3;
        if (used + needed + 1 > output_size) {
            return false;
        }
        if (plain) {
            output[used++] = (char)*cursor;
        } else {
            output[used++] = '%';
            output[used++] = hex[*cursor >> 4];
            output[used++] = hex[*cursor & 0x0f];
        }
    }
    output[used] = '\0';
    return true;
}

static bool bupt_portal_login(void)
{
    if (CONFIG_FOCUSCUBE_BUPT_ACCOUNT[0] == '\0' ||
        CONFIG_FOCUSCUBE_BUPT_PASSWORD[0] == '\0') {
        ESP_LOGE(TAG, "Campus-network credentials are not configured");
        return false;
    }

    portal_response_t probe = {0};
    int status = 0;
    if (!portal_request(CONFIG_FOCUSCUBE_BUPT_PROBE_URL, HTTP_METHOD_GET,
                        NULL, NULL, &probe, &status)) {
        ESP_LOGW(TAG, "Portal probe failed");
        return false;
    }
    if (probe.location[0] == '\0' && status >= 200 && status < 300) {
        ESP_LOGI(TAG, "Campus network is already authenticated");
        return true;
    }
    if (probe.location[0] == '\0') {
        ESP_LOGW(TAG, "Portal redirect missing (HTTP %d)", status);
        return false;
    }

    portal_response_t landing = {0};
    if (!portal_request(probe.location, HTTP_METHOD_GET, NULL, NULL,
                        &landing, &status)) {
        ESP_LOGW(TAG, "Portal landing request failed");
        return false;
    }
    if (landing.cookie[0] == '\0') {
        ESP_LOGW(TAG, "Portal cookie missing");
        return false;
    }
    char *cookie_end = strchr(landing.cookie, ';');
    if (cookie_end != NULL) {
        *cookie_end = '\0';
    }

    char account[192];
    char password[192];
    char post_data[448];
    if (!url_encode(CONFIG_FOCUSCUBE_BUPT_ACCOUNT, account, sizeof(account)) ||
        !url_encode(CONFIG_FOCUSCUBE_BUPT_PASSWORD, password, sizeof(password))) {
        ESP_LOGE(TAG, "Campus-network credential is too long");
        return false;
    }
    snprintf(post_data, sizeof(post_data), "user=%s&pass=%s", account, password);

    portal_response_t login = {0};
    if (!portal_request(CONFIG_FOCUSCUBE_BUPT_LOGIN_URL, HTTP_METHOD_POST,
                        post_data, landing.cookie, &login, &status)) {
        ESP_LOGW(TAG, "Portal login POST failed");
        return false;
    }

    memset(&probe, 0, sizeof(probe));
    if (!portal_request(CONFIG_FOCUSCUBE_BUPT_PROBE_URL, HTTP_METHOD_GET,
                        NULL, NULL, &probe, &status)) {
        return false;
    }
    bool authenticated = probe.location[0] == '\0' && status >= 200 && status < 300;
    ESP_LOGI(TAG, "Campus-network authentication %s (HTTP %d)",
             authenticated ? "succeeded" : "failed", status);
    return authenticated;
}

static void portal_task(void *arg)
{
    while (true) {
        bool authenticated = bupt_portal_login();
        int delay_seconds = authenticated ? CONFIG_FOCUSCUBE_BUPT_RECHECK_SECONDS : 10;
        ESP_LOGI(TAG, "Next campus-network check in %d seconds", delay_seconds);
        vTaskDelay(pdMS_TO_TICKS(delay_seconds * 1000));
    }
}

void app_main(void)
{
    esp_err_t result = nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES ||
        result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(result);

    ESP_LOGI(TAG, "FocusCube S3 campus-network helper starting");
    if (!wifi_connect()) {
        ESP_LOGE(TAG, "Could not connect to %s", CONFIG_FOCUSCUBE_WIFI_SSID);
    }

    BaseType_t task_created = xTaskCreate(portal_task, "bupt_portal", 8192,
                                          NULL, 5, NULL);
    if (task_created != pdPASS) {
        ESP_LOGE(TAG, "Could not create campus-network task");
    }
}

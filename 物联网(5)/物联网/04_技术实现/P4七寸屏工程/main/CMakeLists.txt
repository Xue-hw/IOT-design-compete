idf_component_register(
    SRCS main.c focuscube_data.c fonts/focuscube_font_cn_18.c
    INCLUDE_DIRS . fonts
    REQUIRES esp_http_client esp_wifi esp_event esp_netif nvs_flash
             lvgl__lvgl espressif__cjson waveshare__esp32_p4_wifi6_touch_lcd_7b bsp_extra)

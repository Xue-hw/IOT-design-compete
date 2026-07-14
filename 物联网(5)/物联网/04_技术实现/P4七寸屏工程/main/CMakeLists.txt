set(LV_DEMO_DIR ../managed_components/lvgl__lvgl/demos)
file(GLOB_RECURSE LV_DEMOS_SOURCES ${LV_DEMO_DIR}/*.c)

idf_component_register(
    SRCS main.c fonts/focuscube_font_cn_18.c ${LV_DEMOS_SOURCES}
    INCLUDE_DIRS . fonts ${LV_DEMO_DIR}
    REQUIRES esp_http_client esp_wifi esp_event esp_netif nvs_flash
             lvgl__lvgl espressif__cjson waveshare__esp32_p4_wifi6_touch_lcd_7b bsp_extra)

idf_component_get_property(LVGL_LIB lvgl__lvgl COMPONENT_LIB)
target_compile_options(
    ${LVGL_LIB} 
    PRIVATE
        -DLV_LVGL_H_INCLUDE_SIMPLE
        -DLV_USE_DEMO_MUSIC
)

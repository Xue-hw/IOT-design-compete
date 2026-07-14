# FocusCube 中文字体

`focuscube_font_cn_18.c` 由 Google Noto Sans CJK SC Regular 生成，覆盖 ASCII、常用标点、中点 `·` 和 GB2312 简体中文字符。

- 上游项目：<https://github.com/notofonts/noto-cjk>
- 字体许可：SIL Open Font License 1.1
- 生成工具：`lv_font_conv 1.5.3`
- 生成参数：18 px，4 bpp，RLE 压缩，无 kerning

英文、数字和简体中文由同一字体输出，避免 LVGL 内置 1000 字 SimSun 字库因缺字显示方框或乱码。

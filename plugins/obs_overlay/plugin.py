"""OBS 出力プラグイン: ドット・情報パネル・エンジン名を OBS のブラウザソースに出す切り替えをメイン画面に出す(配信・録画用)。

Universal Engine Dock の表示は画面の取り込みから外しているので、OBS の画面・ウィンドウ・ゲームの取り込みには写らない。
代わりに、この PC だけに見える Web サーバーで同じ見た目の透明な画像を配る(uedock/obs_output.py)。
OBS でブラウザソース(1920×1080)を足し、ゲームの取り込みと一緒に「画面に合わせる」(Ctrl+F)だけで重なる。
"""

from uedock.plugin_api import FEATURE_OBS_OVERLAY, Plugin

PLUGIN = Plugin(
    id="obs_overlay",
    name="OBS 出力",
    description="ドット・情報パネル・エンジン名を OBS のブラウザソースに出します(配信・録画用)",
    features=frozenset({FEATURE_OBS_OVERLAY}),
)

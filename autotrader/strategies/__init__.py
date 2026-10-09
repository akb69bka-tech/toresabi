"""戦略プラグイン。

freqtrade の「戦略クラスを差し替える」設計と、QuantConnect Lean の
「ユニバース選択 → アルファ → ポートフォリオ」の分担を参考にしている。

- 銘柄ごとに判定する戦略: build()/eval() を実装
- 銘柄横断で判定する戦略(ローテーションなど): portfolio() を実装
どちらもシミュレータ・バックテスト・実運用で同じコードを通る。
"""
from .base import Strategy, get_strategy, STRATEGY_TYPES, describe_all

# ENSIM Trade Center — trader `lodesthard`

Trader automatique du TP2 (ENSIM Trade Center, informatique décisionnelle).
Le marché de l'organisateur lit [`decision.json`](decision.json) à chaque tick :

```
https://raw.githubusercontent.com/Lodesthard/ensim-trader-lodesthard/main/decision.json
```

Chaque valeur est une demande dans `[-1, +1]` : positive = acheter `100 × valeur`
unités, négative = vendre `100 × |valeur|` unités, `0` = ne rien faire.

**Tableau de bord :** https://lodesthard.github.io/ensim-trader-lodesthard/

Tous les chiffres ci-dessous viennent de `python scripts/backtest.py` sur
l'historique réel du marché (384 ticks, du 8 septembre au 26 novembre 2026).

---

## 1. Le marché

Lu dans le code de l'organisateur (`exofri/ensim-trade-center`, `scripts/market_tick.py`) :

- prix = 50 % formule cachée « tendance × (1 + amplitude × sinus) » + 50 % prix
  précédent × (1 + sensibilité × demande moyenne de tous les traders), × bruit ;
- l'ordre écrit après le tick *t* est exécuté au prix du tick *t+1* ;
- achat limité à l'argent disponible, vente limitée au stock, produits exécutés
  dans l'ordre A, B, C avec le même argent ; aucun frais ;
- départ : 1 000 € + 1 000 unités de A, B et C ;
- score affiché = argent + stock × dernier prix ;
- trois traders de référence : `random_trader` (ordres au hasard dans [−1, +1]),
  `random_buyer` ([0, 1]), `random_seller` ([−1, 0]).

Le marché tourne en théorie toutes les heures, mais avance en pratique d'un tick
toutes les 5 à 7 heures, avec parfois des rafales de ticks à quelques minutes
d'intervalle.

## 2. Ce que montrent les données

Autocorrélation du prix sans sa tendance (prix / moyenne centrée sur 48 ticks).
Un vrai cycle donne un pic positif fort à la période et un creux négatif fort à
la demi-période.

| Produit | Période | Corr. à la période | Corr. à la ½ période | Lecture |
|---|---|---|---|---|
| A | 48 ticks | +0,85 | −0,92 | cycle net, niveau stable (moyenne 5,01 → 4,99) |
| B | (8) | +0,68 | +0,77 | pas de cycle : hausse continue (8,37 → 12,62 €) |
| C | 24 ticks | +0,93 | −0,96 | cycle très net sur un niveau qui baisse (2,52 → 1,55) |

Volatilité par tick : A 2,0 %, B 1,5 %, C 4,5 %.

## 3. La méthode, étape par étape

```
historique des prix ──► 1. indice S par produit (méthodes d'une même famille)
                      ──► 2. zone morte ajustée selon la réussite
                      ──► 3. taille de l'ordre : 2 × S, plafonnée à ±1
                      ──► 4. règle de fin de partie (8 derniers ticks)
                      ──► 5. garde-fous stock / argent
                      ──► decision.json  +  une ligne dans docs/data/log.csv
```

### 3.1 Indice S par produit — `scripts/signals.py`

Chaque méthode renvoie une valeur dans `[-1, +1]`, exprimée dans la même unité
(écart en écarts-types, divisé par 2 et borné), pour que les poids et la zone
morte aient le même sens pour toutes les méthodes et tous les produits :

```
S = Σ (poids_i × signal_i) / Σ poids_i
```

On ne mélange que des méthodes **d'une même famille**. Le suivi de tendance et le
retour à la moyenne font des paris opposés : sur une même fenêtre, les combiner
revient à affaiblir une seule méthode.

| Produit | Famille | Méthodes (poids) | Justification |
|---|---|---|---|
| A | retour à la moyenne | Bollinger 48 (100), écart au lissage exponentiel α = 0,05 sur 48 (100), écart à la moyenne de tout l'historique (100) | cycle de 48 ticks, niveau stable : fenêtres = un cycle |
| B | aucune | — (S = 0) | hausse lente : on détient déjà 1 000 B et 1 000 € n'en achètent qu'environ 80 de plus, donc toute vente fait rater la hausse |
| C | retour à la moyenne | Bollinger 24 (100), lissage exponentiel α = 0,08 sur 24 (100), moyenne historique (0) | cycle de 24 ticks ; moyenne historique exclue car le niveau baisse |

Chaque méthode testée seule, sans zone morte, ordres ×2 (gain en € par rapport à
« ne rien faire », 1re moitié / 2e moitié de l'historique) :

| Méthode | A | B | C |
|---|---|---|---|
| Bollinger | +6 289 / +5 274 | −1 185 / −2 186 | +4 059 / +1 615 |
| Écart au lissage exponentiel | +5 820 / +4 664 | −1 266 / −2 185 | +4 224 / +1 690 |
| Écart à la moyenne historique | +6 313 / +5 222 | −1 159 / −2 637 | +1 723 / −303 |
| Momentum 24 ticks | −2 986 / −2 761 | −355 / −196 | +494 / +576 |
| Croisement de moyennes 6/24 | −2 197 / −2 112 | +527 / +578 | −2 597 / −1 576 |
| Pente de régression 48 | −2 492 / −2 492 | −2 319 / −2 628 | −223 / +336 |
| *random_trader (moyenne de 300 tirages)* | *+18 / −20* | *−250 / −696* | *+79 / +87* |

Le retour à la moyenne gagne nettement sur A et C dans les deux moitiés. Sur B,
rien n'est stable d'une moitié à l'autre : B ne trade pas.

### 3.2 Zone morte — base 0,20

Sous la zone morte, on ne fait rien. Gain total (A + B + C) selon la zone morte,
configuration retenue, ordres ×2 :

| Zone morte | 1re moitié | 2e moitié | Trades (2e moitié) |
|---|---|---|---|
| 0 | +10 579 € | +7 985 € | 317 |
| 0,10 | +10 540 € | +8 003 € | 282 |
| **0,20** | **+10 513 €** | **+7 941 €** | **253** |
| 0,30 | +10 343 € | +7 482 € | 220 |
| 0,50 | +8 277 € | +4 986 € | 128 |
| 0,65 | +2 601 € | +1 473 € | 45 |

- Sans frais, chaque petit trade rapporte encore un peu : le gain baisse dès
  qu'on élargit la zone morte.
- La moyenne de trois méthodes écrase l'indice : |S| ne dépasse jamais environ
  0,8. Une zone morte à 0,67 (idée de départ) ne laisse passer que les extrêmes.
- 0,20 garde presque tout le gain avec 20 % de trades en moins. Moins de trades,
  c'est aussi moins d'effet de nos propres ordres sur le prix (non simulé).

### 3.3 Ajustement selon la réussite — `scripts/adaptive.py`

Pour chaque produit et **séparément pour les achats et les ventes**, on juge les
signaux des 48 derniers ticks : un signal d'achat est réussi si le prix a monté
un quart de cycle après l'exécution (12 ticks pour A, 6 pour C), un signal de
vente s'il a baissé.

```
seuil = 0,20 + 1,5 × (50 % − taux de réussite), borné entre 0,10 et 0,90
réussite ≥ 57 % → 0,10 · 50 % → 0,20 · 30 % → 0,50 · 20 % → 0,65
```

Choix de conception :

- on mesure la **qualité des décisions**, pas « sommes-nous en positif sur ce
  produit » : au départ on détient 1 000 unités de chaque, donc B serait « en
  positif » et C « en négatif » sans aucun trade ;
- on ne change **jamais la taille** des ordres (pas de « j'ai gagné, j'achète
  plus », qui sur un cycle reviendrait à acheter au sommet) ;
- fenêtre glissante et bornes : une mauvaise série ne bloque pas un produit pour
  toujours, et une ancienne série est oubliée ;
- seuils séparés : si les ventes marchent et pas les achats, seul le seuil
  d'achat monte ;
- les signaux passés sont recalculés à partir des prix, donc la mesure marche
  dès la première exécution.

Test sur la 2e moitié, y compris avec un signal volontairement cassé :

| Scénario | Zone morte fixe 0,20 | Selon la réussite |
|---|---|---|
| Signal normal | +7 941 € | +7 836 € |
| Signal inversé | −4 313 € | −1 091 € |
| Signal cassé à partir du tick 300 | 0 € | +1 587 € |

C'est une assurance : −1,3 % quand le signal marche, l'essentiel des pertes évité
s'il cesse de marcher. Une 1re version bornée à 0,45 ne protégeait pas du tout :
les gros ordres perdants ont |S| > 0,45.

### 3.4 Taille des ordres — ×2

```
ordre = 2 × S, plafonné à ±1      (S = 0,5 → 100 unités ; S = 0,25 → 50)
```

Choix volontairement agressif. 2e moitié, zone morte 0,20 :

| Taille | Gain | Pire baisse depuis un sommet |
|---|---|---|
| S ÷ 2 | +2 287 € | −14,4 % |
| S × 1 | +4 565 € | −11,9 % |
| **S × 2** | **+7 941 €** | **−12,0 %** |
| ne rien faire | 0 € | −16,8 % |

Plus gros ordres, mais pas plus de risque mesuré : on vend A et C près de leurs
sommets, ce qui amortit les baisses du portefeuille.

### 3.5 Règle de fin de partie — `scripts/endgame.py`

La partie se termine le **mardi 1er décembre 2026 à 15:00 (Paris)**. À la fin,
l'argent ne rapporte rien et un achat tardif n'a plus le temps de remonter.

- **Prévision :** pour A et C, modèle `(niveau + pente × t) × (1 + amplitude × sin(2πt / période))`,
  ajusté par moindres carrés (R² = 0,98 pour A, 0,998 pour C). Prix final prévu =
  moyenne des prévisions à ± 3 ticks de la fin estimée.
- **Ticks restants :** heures restantes ÷ rythme du marché sur les 72 dernières heures.
- **Pendant les 8 derniers ticks**, la prévision remplace le signal pour A et C :
  - vendre si le prix est au-dessus du prix final prévu **et** proche du meilleur
    prix encore attendu (sinon on attend le sommet) ;
  - acheter si le prix est en dessous du prix final prévu **et** proche du plus
    bas encore attendu ;
  - taille : 5 % d'écart → 100 unités ;
  - sinon, conserver.
- **B :** pendant les 6 derniers ticks, l'argent inutilisé est placé en B
  (60 unités par tick au maximum), car B monte en moyenne et l'argent non.

Test : on fait comme si la partie s'arrêtait à 26 ticks passés différents.

| Réglage | Gain moyen ajouté | 4 ticks de moins que prévu | 4 ticks de plus que prévu |
|---|---|---|---|
| achats bloqués, 12 ticks (1re version, ordres ÷ 2) | +165 € (14/24 positifs) | — | — |
| symétrique, 12 ticks (ordres ×2) | +79 € | −23 € | +52 € |
| **symétrique, 8 ticks (ordres ×2)** | **+184 € (23/26)** | **+129 €** | **+39 €** |

12 ticks, c'est un demi-cycle de C : la règle y remplaçait le signal sur toute
une phase de C. 8 ticks reste en dessous.

### 3.6 Garde-fous — `scripts/algorithm.py`

- jamais vendre plus que le stock détenu ;
- jamais dépenser sous une réserve de 50 € ; l'argent est partagé entre A, B, C
  dans l'ordre d'exécution de l'organisateur.

## 4. Exécution automatique

`.github/workflows/auto_decide.yml`, toutes les 30 minutes :

1. `scripts/algorithm.py` lit le marché, nos avoirs et le classement, écrit
   `decision.json` avec une raison en français par produit, et ajoute une ligne
   à `docs/data/log.csv` à chaque nouveau tick du marché (horodatage, prix,
   argent, stocks, valeur totale, valeur des trois références, S, ordres, phase,
   ticks restants, raisons) ;
2. `scripts/reference_history.py` reconstruit la valeur totale de chaque trader
   tick par tick depuis le tick 1, à partir de l'historique Git du classement de
   l'organisateur (`docs/data/leaderboard.json` y est commité à chaque tick) →
   `docs/data/reference_history.csv` ;
3. `scripts/build_dashboard.py` régénère `docs/data/dashboard_data.js` ;
4. le workflow commite `decision.json` et `docs/data/`. Les étapes 2 et 3 ne
   peuvent pas bloquer le commit de la décision.

## 5. Tableau de bord — `docs/index.html`

| Section | Contenu | Décision aidée |
|---|---|---|
| Synthèse | valeur réelle, rang, écart vs « ne rien faire » et vs `random_trader`, décision publiée, compte à rebours de fin de partie | où en est-on ? |
| 1. Performance | valeur réelle et des trois références depuis le tick 1 (valeur ou écart vs « ne rien faire »), stratégie simulée, classement en direct | garder ou couper le signal |
| 2. Risque | drawdown et volatilité de chaque trajectoire (nous, références, 1er élève, stratégie simulée), drawdown dans le temps, volatilité glissante par produit | quel compromis rendement / risque viser |
| 3. Signal par produit | prix, bandes de Bollinger, trades, prévision de fin, indice S avec sa zone morte ajustée | acheter, vendre ou attendre |
| 4. Journal | décisions tick par tick avec leur raison (simulé et réel) | comprendre chaque trade |
| 5. Méthode | cycles, méthodes testées, zone morte, ajustement selon la réussite, règle de fin, limites | justifier les choix |

## 6. Résultat simulé sur tout l'historique

Ticks 50 à 384, stratégie complète (sans la règle de fin, qui ne s'applique qu'à
la vraie fin) : **37 072 € contre 19 631 € pour « ne rien faire »** (+17 442 €),
559 trades, pire baisse −12,9 % contre −17,8 %.

Trajectoires réelles reconstruites depuis le classement de l'organisateur
(ticks 1 à 384) :

| Trajectoire | Début → fin | Drawdown max | Volatilité / tick |
|---|---|---|---|
| Ne rien faire | 17 374 € → 19 631 € (+13,0 %) | −17,8 % | 1,12 % |
| random_trader | 17 374 € → 19 205 € (+10,5 %) | −18,4 % | 0,96 % |
| random_buyer | 17 374 € → 19 538 € (+12,5 %) | −19,1 % | 1,19 % |
| random_seller | 17 374 € → 18 006 € (+3,6 %) | −0,5 % | 0,09 % |
| 1er élève | 17 374 € → 21 853 € (+25,8 %) | −16,8 % | 1,00 % |

Notre trader n'a été inscrit au classement par l'organisateur qu'au tick 382,
avec la dotation de départ (1 000 € + 1 000 unités de chaque produit).

## 7. Limites

- **Notre effet sur le prix n'est pas simulé.** Le moteur ajoute la demande
  moyenne de tous les traders au prix. Avec des ordres ×2, un ordre de 100 unités
  pèse environ 1/20 de la demande moyenne : le gain réel sera plus faible que le
  test.
- **Test pas totalement hors échantillon.** Les périodes de cycle (48 et 24) ont
  été mesurées sur tout l'historique, et certains réglages (taille ×2, fenêtre de
  fin à 8 ticks, sensibilité de l'ajustement) ont été choisis en comparant
  plusieurs valeurs sur ce même historique. Les deux moitiés donnent des
  résultats cohérents, mais ce n'est pas une preuve stricte.
- **Fenêtres non optimisées.** Une fenêtre Bollinger de 36 ticks ferait mieux
  sur A ; on garde 48, la période du cycle, parce que c'est justifiable.
- **Nombre de ticks restants estimé.** Le rythme du marché est irrégulier ; la
  règle de fin reste gagnante en moyenne avec 4 ticks d'erreur dans chaque sens.
- **L'argent liquide limite les achats** (1 000 € au départ) : les gros ordres
  d'achat sont souvent réduits tant que des ventes n'ont pas rapporté d'argent.

## 8. Reproduire

```
python scripts/backtest.py               # historique en ligne
python scripts/backtest.py market.json   # fichier local
python scripts/build_dashboard.py        # régénère docs/data/dashboard_data.js
python scripts/algorithm.py              # une décision réelle (écrit decision.json et log.csv)
```

| Fichier | Rôle |
|---|---|
| `scripts/signals.py` | méthodes, configuration par produit, zone morte, taille des ordres |
| `scripts/adaptive.py` | zone morte selon la réussite |
| `scripts/endgame.py` | modèle de prévision et règle de fin de partie |
| `scripts/algorithm.py` | décision réelle, garde-fous, journal |
| `scripts/backtest.py` | rejoue les règles de l'organisateur sur l'historique |
| `scripts/reference_history.py` | trajectoires réelles de tous les traders depuis le tick 1 |
| `scripts/build_dashboard.py` | données du tableau de bord |
| `docs/index.html` | tableau de bord |

"""One-shot helper for Phase 14.6 — merge new FR namespaces into
`frontend/src/locales/fr.json`. Not wired into the build; we run
it once when new top-level i18n namespaces land in EN."""

from __future__ import annotations

import json
from pathlib import Path

FR_ADDITIONS: dict[str, dict] = {
    "reliability": {
        "title": "Santé et fiabilité Kubernetes",
        "subtitle": (
            "Risques workload et infrastructure, classés selon la vue "
            "de l'agent sur le cluster."
        ),
        "workload": {"title": "Santé des workloads"},
        "infra": {"title": "Santé de l'infrastructure"},
        "section": {
            "realtime": "Problèmes en temps réel",
            "risks": "Risques de fiabilité",
        },
        "tiles": {
            "unhealthyServices": "Pods en panne",
            "activeIncidents": "Incidents actifs",
            "forecastedBreaches": "Dépassements prévus",
            "podRestarts": "Pods redémarrés",
            "nodesNotReady": "Nœuds non prêts",
            "nodesWithPressure": "Nœuds sous pression",
            "agentMode": "Mode de l'agent",
            "baselinesKnown": "Baselines connues",
            "allHealthy": "Tous en marche",
            "unhealthyHint": "à travers {{count}} namespace(s)",
            "noSurge": "Aucun pic prévu",
            "surgeWindow": "Horizon 5 min",
            "restartsHint": "ce run",
            "pressureHint": "mémoire / disque / PID",
            "baselinesHint": "tuples (metric, ns, pod)",
        },
        "risks": {
            "degradedServices": "Services dégradés",
            "nodePressure": "Pression sur les nœuds",
        },
        "chart": {
            "title": "Problèmes au fil du temps · {{count}} derniers jours",
            "healed": "Réparés",
            "auto": "Auto-résolus",
            "escalated": "Escaladés",
            "failed": "Échoués",
        },
    },
    "optimization": {
        "title": "Optimisation",
        "subtitle": (
            "Recommandations de dimensionnement issues des baselines apprises. "
            "À l'opérateur — appliquer via l'assistant Cas d'usage ou `kubectl patch`."
        ),
        "summary": {
            "recommendations": "Recommandations",
            "over": "Sur-provisionnés",
            "overHint": "Réduire les requests pour libérer de la capacité",
            "under": "Sous-provisionnés",
            "underHint": "Augmenter les limits pour éviter les OOM",
            "avgHeadroom": "Marge moyenne",
        },
        "table": {
            "title": "Recommandations par workload",
            "empty": (
                "Pas encore de recommandations — le learner a besoin de ≥ 50 "
                "échantillons par métrique avant de pouvoir dimensionner."
            ),
            "emptyAction": "Retour au tableau de bord",
        },
        "cols": {
            "workload": "Workload",
            "direction": "Direction",
            "mean": "Moyenne baseline",
            "current": "Actuel",
            "sigma": "σ au-dessus",
            "headroom": "Marge",
        },
        "legend": "{{count}} workloads bien dimensionnés · classés par urgence",
    },
    "settings": {
        "title": "Paramètres",
        "subtitle": (
            "Coupe-circuit de l'agent, calibration, configuration runtime, "
            "journal d'audit, et informations produit."
        ),
        "tabs": {
            "agent": "Agent",
            "calibration": "Calibration",
            "forecasting": "Prédiction",
            "baselines": "Baselines",
            "notifications": "Notifications",
            "audit": "Audit",
            "about": "À propos",
        },
        "agent": {
            "mode": {
                "title": "Mode de l'agent",
                "description": (
                    "Coupe-circuit pour l'agent. `observe-only` = dry-run ; "
                    "`active` permet l'exécution ; `frozen` bloque tout y compris "
                    "la mise en astreinte."
                ),
                "option": {
                    "observe-only": "Observation seule",
                    "active": "Actif",
                    "frozen": "Gelé",
                },
            },
            "approvals": {
                "title": "Approbations en attente",
                "empty": "Aucune action en attente d'approbation opérateur.",
                "approve": "Approuver",
                "deny": "Refuser",
                "denyReason": "Raison…",
            },
            "confirmTitle": "Passer l'agent en {{mode}} ?",
            "confirmActive": (
                "Le mode actif autorise l'agent à exécuter les remédiations "
                "de façon autonome. OPA filtre toujours chaque action."
            ),
            "confirmFrozen": (
                "Le mode gelé bloque TOUTES les actions, y compris page_human. "
                "Utilisez-le pour reprendre la main pendant un incident."
            ),
            "confirmObserve": (
                "Observe-only = dry-run ; les actions sont consignées mais pas exécutées."
            ),
            "confirmApply": "Appliquer",
        },
        "calibration": {
            "summary": {
                "median": "Confiance médiane",
                "medianHint": "Cible ≥ 0,90",
                "above90": "Règles au-dessus de 90%",
                "rules": "Règles suivies",
            },
            "table": {
                "title": "Calibration par règle",
                "empty": "Aucune règle évaluée — lancez un cas d'usage pour amorcer la collecte.",
            },
            "cols": {
                "rule": "Règle",
                "n": "N",
                "accuracy": "Précision",
                "p50": "p50",
                "p90": "p90",
                "threshold": "Seuil effectif",
                "source": "Source",
                "override": "Override",
            },
            "source": {
                "override": "override opérateur",
                "learned": "appris (p25)",
                "global": "défaut global",
            },
            "pin": "Épingler",
        },
        "knobs": {
            "forecasting": {
                "title": "Prédiction",
                "config": (
                    "À modifier dans `.env.local` (laptop) ou le `values.yaml` "
                    "Helm (cluster), puis redémarrer le conteneur api."
                ),
            },
            "baselines": {
                "title": "Apprentissage des baselines",
                "config": (
                    "Les baselines nécessitent Prometheus. Appliquez "
                    "manifests/monitoring.yaml pour l'installation in-cluster (Phase 13c)."
                ),
            },
            "notifications": {
                "title": "Notifications",
                "config": (
                    "Les deux URLs sont optionnelles ; on peut les définir simultanément. "
                    "Toutes deux reçoivent le même payload JSON quand `page_human` se déclenche."
                ),
            },
            "forecastTick": {
                "label": "Cadence du planificateur",
                "hint": "Fréquence à laquelle le forecaster parcourt chaque baseline ajustée.",
            },
            "forecastWindow": {
                "label": "Fenêtre d'ajustement",
                "hint": "Échantillons par ajustement OLS. Plus grand = plus lisse, plus lent.",
            },
            "forecastDedup": {
                "label": "Fenêtre anti-doublon",
                "hint": "Délai par (métrique, niveau) avant nouvelle publication.",
            },
            "prometheusUrl": {
                "label": "URL Prometheus",
                "hint": (
                    "URL httpx directe ou schéma `k8s-proxy://`. "
                    "Vide désactive baselines + forecasts."
                ),
            },
            "scrapeInterval": {
                "label": "Intervalle de scrape",
                "hint": "Fréquence à laquelle le learner interroge Prometheus.",
            },
            "queryProfile": {
                "label": "Profil de requêtes",
                "hint": (
                    "`k8s` pour Prometheus in-cluster (labels pod) ; "
                    "`docker` pour cAdvisor côté compose."
                ),
            },
            "slack": {
                "label": "Webhook Slack",
                "hint": "Là où `page_human` poste lors d'une escalade.",
            },
            "generic": {
                "label": "Webhook générique",
                "hint": "PagerDuty / Opsgenie / Discord / Teams / collecteur JSON personnalisé.",
            },
            "alertmanager": {
                "label": "Bearer Alertmanager",
                "hint": "Authentification du webhook entrant /api/webhooks/alertmanager.",
            },
        },
        "audit": {"empty": "Aucune entrée d'audit pour l'instant."},
        "about": {
            "product": {
                "title": "Produit",
                "name": "Nom",
                "platform": "Plateforme",
                "company": "Société",
                "version": "Version",
                "license": "Licence",
            },
            "deployment": {
                "title": "Déploiement",
                "scope": "Portée",
                "scopeValue": "Cluster edge mono-tenant",
                "scopeHint": "Une installation par cluster Kubernetes — pas un SaaS.",
                "firstTarget": "Premier matériel cible",
                "jetson": "NVIDIA Jetson Orin Nano (édition GPU)",
                "jetsonHint": "LLM local (Qwen2.5-7b q4_K_M) sur CUDA ; fallback CPU pris en charge.",
                "properties": "Propriétés",
            },
            "props": {
                "autonomous": "Autonome",
                "resilient": "Résilient",
                "secure": "Sécurisé",
                "smart": "Intelligent",
                "learning": "Apprenant",
            },
            "loop": {
                "title": "La boucle de l'agent",
                "observe": "Observer",
                "observeDesc": (
                    "Événements kubelet + diagnostics K8sGPT + baselines Prometheus "
                    "alimentent le triage."
                ),
                "predict": "Prédire",
                "predictDesc": (
                    "Régression linéaire sur chaque baseline ; horizons 5/15/60 min "
                    "injectés dans le prompt diagnose."
                ),
                "heal": "Réparer",
                "healDesc": (
                    "Qwen-2.5-7b local choisit une action parmi le catalogue de 10 outils ; "
                    "matrice OPA filtre l'exécution."
                ),
                "learn": "Apprendre",
                "learnDesc": (
                    "La calibration par règle se met à jour à chaque incident — "
                    "cible de 90 % de confiance médiane."
                ),
            },
        },
    },
    "workload": {
        "back": "Retour au cluster",
        "header": {
            "health": "Santé",
            "replicas": "Réplicas",
            "age": "Âge",
            "restarts": "Redémarrages",
            "node": "Nœud",
            "lastAction": "Dernière action",
            "image": "Image",
            "activeIncidents": "actif",
            "history": "Historique",
            "incidentsCount": "incidents",
            "healed": "réparés",
            "escalated": "escaladés",
            "failed": "échoués",
        },
        "ribbon": {
            "title": "Événements · 24 dernières heures",
            "empty": "Aucun incident dans cette fenêtre — le workload est sain.",
            "healed": "réparé",
            "escalated": "escaladé",
            "failed": "échoué",
            "auto": "auto",
        },
        "tabs": {
            "incidents": "Incidents",
            "baselines": "Baselines",
            "forecasts": "Prédictions",
        },
        "incidents": {"empty": "Aucun incident enregistré pour ce workload."},
        "baselines": {
            "title": "Baselines de métriques",
            "empty": "Pas encore de baselines — le learner a besoin d'environ 50 échantillons par métrique.",
        },
        "forecasts": {
            "title": "Horizons de prédiction",
            "empty": "Pas encore de prédictions pour ce workload.",
        },
    },
    "dashboard": {
        "title": "Vue d'ensemble du cluster edge",
        "subtitle": "Observer → Prédire → Réparer → Apprendre — ce que l'agent voit en ce moment.",
        "hero": {
            "mode": "Mode de l'agent",
            "modeObserveHint": "Dry-run ; aucune mutation.",
            "modeActiveHint": "Remédiation autonome activée.",
            "modeFrozenHint": "Coupe-circuit engagé.",
            "pods": "Pods prêts",
            "podsHint": "Tous les workloads",
            "active": "Incidents actifs",
            "activeQuiet": "Calme",
            "activeBusy": "Runs de graphe en cours",
            "confidence": "Confiance médiane",
            "confidenceHint": "Cible ≥ 0,90",
            "baselines": "Baselines",
            "baselinesHint": "{{count}} échantillons",
            "forecasts": "Alertes de prédiction",
            "forecastsHint": "Warning + critical",
        },
        "active": {
            "title": "Incidents actifs",
            "empty": "Aucun incident en vol — l'agent est au repos.",
        },
        "recent": {"title": "Résultats récents", "empty": "Aucun incident enregistré."},
        "forecast": {
            "title": "Liste de surveillance des prédictions",
            "loading": "Chargement des prédictions…",
            "calm": "Aucun pic prévu sur les métriques suivies.",
        },
        "problemMap": {
            "title": "Santé Namespace × Ressource",
            "empty": "Aucun namespace visible — `make cluster` est-il lancé ?",
            "cols": {
                "namespace": "Namespace",
                "pods": "Pods",
                "deployments": "Deployments",
                "incidents": "Incidents actifs",
            },
        },
    },
}


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    fr_path = root / "frontend" / "src" / "locales" / "fr.json"
    fr = json.loads(fr_path.read_text(encoding="utf-8"))
    for k, v in FR_ADDITIONS.items():
        fr[k] = v
    # Drop legacy `control` namespace — the route is now a redirect
    # into /settings?tab=agent; calibration content lives under
    # /settings?tab=calibration.
    fr.pop("control", None)
    fr_path.write_text(
        json.dumps(fr, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"FR updated. Top-level keys: {sorted(fr.keys())}")


if __name__ == "__main__":
    main()

//+------------------------------------------------------------------+
//|                  Guardian_D017_PropFirmAuto_v11_16_MOMENTUM_PROD.mq5                   |
//|      Guardian FTMO multi-paires Forex/Crypto — version nettoyée   |
//|      Crypto desk limits | cooldown pertes | SL floor | Forex intact     |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, FTMO Guardian Institutional PRO"
#property link      "https://ftmo.com"
#property version   "11.16"
#property strict

#include <Trade/Trade.mqh>

//--- ENUMS STRATÉGIE & GUARDIAN
enum ENUM_STRATEGY_MODE
  {
   MODE_TREND_ONLY=0,        // Recommandé FTMO : Breakout + Pullback en tendance
   MODE_ADAPTIVE_REGIME,     // Automatique selon ADX / régime (seuil InpADX_TrendThreshold)
   MODE_BREAKOUT_ONLY,       
   MODE_PORTFOLIO_RANKED,    // Portefeuille : candidats classés contextuellement
   MODE_MANUAL_GUARDIAN_ONLY // Protège uniquement vos ordres manuels
  };

enum ENUM_FTMO_RULESET
  {
   FTMO_RULESET_2_STEP=0,
   FTMO_RULESET_1_STEP=1
  };

//--- PropFirmGuard / account auto-detection
enum ENUM_PROP_FIRM
  {
   PROP_FIRM_AUTO=0,
   PROP_FIRM_FTMO,
   PROP_FIRM_FUNDEDNEXT,
   PROP_FIRM_THE5ERS,
   PROP_FIRM_OTHER
  };

enum ENUM_PROP_PROFILE
  {
   PFG_PROFILE_AUTO=0,
   PFG_FTMO_2STEP_CHALLENGE,
   PFG_FTMO_2STEP_FUNDED_STANDARD,
   PFG_FTMO_2STEP_FUNDED_SWING,
   PFG_FUNDEDNEXT_FREE_TRIAL,
   PFG_FUNDEDNEXT_STELLAR_2STEP_CHALLENGE,
   PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED,
   PFG_THE5ERS_HIGH_STAKES,
   PFG_PROFILE_UNKNOWN
  };

enum ENUM_PROP_NEWS_POLICY
  {
   PROP_NEWS_NONE=0,
   PROP_NEWS_FTMO_RESTRICTED_2MIN,
   PROP_NEWS_FUNDEDNEXT_REWARD_5MIN
  };

enum ENUM_AUTO_STRATEGY
  {
   STRAT_BREAKOUT=0,
   STRAT_PULLBACK,
   STRAT_SWEEP,
   STRAT_MOMENTUM
  };

enum ENUM_ASSET_PROFILE
  {
   PROFILE_CRYPTO_LIQUID=0,
   PROFILE_CRYPTO_MAJOR,
   PROFILE_CRYPTO_LIQUID_ALT,
   PROFILE_CRYPTO_DISTINCT,
   PROFILE_CRYPTO_HIGH_BETA,
   PROFILE_CRYPTO_OTHER,
   PROFILE_FOREX,
   PROFILE_GOLD,
   PROFILE_OIL,
   PROFILE_INDEX,
   PROFILE_OTHER
  };

enum ENUM_MARKET_CLASS
  {
   MARKET_CRYPTO=0,
   MARKET_FOREX,
   MARKET_GOLD,
   MARKET_OIL,
   MARKET_INDEX,
   MARKET_OTHER
  };

enum ENUM_MARKET_REGIME
  {
   REGIME_TREND=0,
   REGIME_RANGE,
   REGIME_HIGH_VOL_TREND,
   REGIME_HIGH_VOL_RANGE,
   REGIME_LOW_VOL,
   REGIME_UNKNOWN
  };

enum ENUM_SIGNAL_GRADE
  {
   SIGNAL_C=0,
   SIGNAL_B,
   SIGNAL_A,
   SIGNAL_A_PLUS
  };

struct SignalCandidate
  {
   bool              valid;
   ENUM_ORDER_TYPE   order_type;
   string            engine;
   string            reason;
   double            score;
   ENUM_SIGNAL_GRADE grade;
   double            risk_factor;
   double            sl_dist;
  };

enum ENUM_FTMO_GUARDIAN_STATE
  {
   GUARDIAN_NORMAL=0,
   GUARDIAN_WARNING,
   GUARDIAN_RESTRICTED,
   GUARDIAN_FORCE_CLOSE,
   GUARDIAN_LOCKED
  };

enum ENUM_GUARDIAN_DECISION
  {
   DECISION_ALLOW=0,
   DECISION_BLOCK
  };

//--- INPUTS : PROP FIRM + GESTION DES RISQUES
input group "=== [0] PROP FIRM GUARD / AUTO-DETECTION ==="
input bool              InpEnablePropFirmAutoDetect = true;
input ENUM_PROP_FIRM    InpPropFirmOverride         = PROP_FIRM_AUTO;
input ENUM_PROP_PROFILE InpPropProfileOverride      = PFG_PROFILE_AUTO;
input bool              InpEnablePropFirmRuntimeAlerts = true; // FILE_COMMON\PropFirmGuard
input bool              InpBlockAutoTradingOnUnknownPropProfile = true;
input bool              InpFundedNextEAUsageAuthorized = false; // TRUE uniquement si l'option/autorisation EA est active sur le compte
input bool              InpProtectFundedNextFundedNewsWindow = true; // évite la fenêtre économique +/-5 min
input int               InpPropFirmRuntimeScanSeconds = 30;
input bool              InpBootstrapPortfolioCharts = false; // démarrage multi-graphiques explicite via preset local
input string            InpBootstrapPortfolioSymbols = "EURUSD,GBPUSD,USDCHF";

input group "=== [1] GUARDIAN CORE / LIMITES DE REPLI ==="
input double            InpInitialCapitalOverride = 0.0; // 0 = capital initial réel du Strategy Tester / compte
input ENUM_FTMO_RULESET InpFTMORuleSet           = FTMO_RULESET_2_STEP; // Repli FTMO si profil auto non résolu
input double            InpFTMODailyLossPct       = 5.0;    // Repli hard daily (%)
input double            InpGuardianDailyStopPct   = 4.8;    // Fermeture de sécurité avant la limite FTMO (%)
input double            InpFTMOOverallLossPct     = 10.0;   // Repli hard overall (%)
input double            InpGuardianOverallStopPct = 8.5;    // Guardian Soft Stop Total (%)
input double            InpRiskPerTradePct        = 0.25;   // Risque par trade (%) — défaut conservateur prop
input double            InpMinTradeRiskUSD       = 25.0;   // Aucun trade sous ce risque SL (hors commission)
input bool              InpScaleMinTradeRiskWithCapital = true; // Le plancher 25$ validé sur 100k conserve son ratio (0,025%)
input double            InpMaxOpenAccountRiskPct  = 1.00;   // Risque cumulé max simultané (%)
input int               InpMaxAccountPositions    = 3;      // Cap anti-surexposition compte
input int               InpMaxSymbolPositions     = 1;      // Cap anti-empilement par symbole
input bool              InpUseFallbackCommissionModel = true; // Inclut les frais FTMO si le testeur les omet
input double            InpForexCommissionPerLotPerSide = 2.50; // USD/lot/côté (FTMO)
input double            InpCryptoCommissionPctPerSide = 0.0325; // % du notionnel/côté (FTMO)
input int               InpMaxSymbolPositionsCrypto = 1;    // Crypto: max positions par symbole (anti-stack)
input int               InpMaxCryptoSameDir      = 1;      // Crypto: max BUY ou SELL net sur le symbole
input int               InpMaxCryptoOpenTotal    = 2;      // Crypto: max positions crypto compte (tous symboles)
input int               InpConsecutiveLossCooldown = 3;    // Pause après N pertes d'affilée (0=off)
input int               InpCooldownMinutesAfterLosses = 180; // Durée pause (min) — prop-style cooling
input int               InpCryptoCooldownMinutes  = 240;  // Pause crypto un peu plus longue
input double            InpCryptoSLFloorATR      = 1.25;  // Plancher SL crypto en ATR (anti-bruit 60$)
input double            InpCryptoSLCapATR        = 3.50;  // Plafond SL crypto en ATR (lot calculable)
input bool              InpBlockCryptoAgainstMacro = true; // Pas de trade crypto contre macro H1
input bool              InpEnableCryptoDebugLogs = true; // Journaux détaillés des filtres crypto
input bool              InpEnableExitDebugLogs   = false; // Diagnostic sorties uniquement, sans modifier les règles
input int               InpMaxTradesPerDayCrypto = 8;     // Cap entrées crypto / jour Prague (0=off)
input bool              InpEmergencyCloseWholeAccount = true; // Fermer aussi manuels/autres EA au seuil Guardian

input group "=== [2] GESTION DES ORDRES MANUELS ==="
input bool              InpAdoptManualTrades      = true;   // Gérer les trades manuels (Magic 0)
input bool              InpEnableAccountWideManualGuardian = true; // Un owner unique protège les Magic 0 de TOUT le compte
input int               InpManualGuardianTimerMs  = 250;    // Surveillance indépendante des ticks du graphique
input int               InpManualOwnerStaleSeconds= 3;      // Reprise automatique si l'owner disparaît
input int               InpManualProtectionFailCloseMs = 3000; // Si aucune protection n'est possible après ce délai: fermeture sécurité
input bool              InpAutoAddSLToManual      = true;   // Poser automatiquement un SL si absent
input bool              InpAutoAddTPToManual      = false;  // v11.16: legacy, pas de TP broker intégral; TP partiel logiciel + runner
input double            InpManualSL_ATR_Mult      = 1.00;   // SL manuel FX/or/index (défaut)
input double            InpManualSL_ATR_MultCrypto= 1.75;   // SL manuel crypto (bruit M5 plus large)
input double            InpManualSL_ATR_MultGold  = 1.20;   // SL manuel or
input double            InpManualSL_ATR_MultOil   = 1.40;   // SL manuel pétrole
input double            InpManualMaxRiskPct       = 0.50;   // Risque max manuel = % du capital de référence
input double            InpManualTP_R             = 2.00;   // TP partiel FX (R + frais)
input double            InpManualTP_R_Crypto      = 2.50;   // TP partiel crypto
input double            InpManualTP_R_Gold        = 2.00;   // TP partiel or
input double            InpManualTP_ClosePercent  = 50.0;   // Part encaissée au TP (FX/or)
input double            InpManualTP_ClosePctCrypto= 40.0;   // Part encaissée crypto (laisse plus de runner)
input double            InpManualBE_Trigger_R     = 1.15;   // BE FX/or
input double            InpManualBE_Trigger_R_Crypto = 1.25; // BE crypto (un peu plus loin)
input double            InpManualMinSpreadFactor  = 1.50;   // Distance SL minimale >= spread x facteur
input int               InpManualMinExtraPoints  = 2;      // Marge supplémentaire au-dessus Stops/Freeze
input int               InpBE_BufferPoints        = 25;     // Marge réelle anti-spread et commissions

input group "=== [3] LISSAGE FIN DE CHALLENGE (SMOOTH LANDING) ==="
input bool              InpEnableSmoothLanding    = true;   
input double            InpSmoothLandingTriggerPct= 7.5;    // Activer si profit >= 7.5% (Phase 1)
input double            InpSmoothLandingRiskPct   = 0.25;   // Réduire le risque à 0.25%

input group "=== [3B] RÉDUCTION DU RISQUE EN DRAWDOWN ==="
input bool              InpEnableDailyRiskScaling = true;
input double            InpDDRiskStep1Pct         = 2.0;    // Perte journalière : risque réduit
input double            InpDDRiskStep1TradePct    = 0.25;
input double            InpDDRiskStep2Pct         = 3.5;    // Perte journalière : risque minimal
input double            InpDDRiskStep2TradePct    = 0.10;
input double            InpStopNewTradesDailyPct  = 4.0;    // Plus aucune entrée avant le coupe-circuit 4.8%

input group "=== [4] SÉCURITÉ DE SESSION ==="
input bool              InpProtectRolloverWindow  = true;   // Bloquer 23:45 -> 00:15 Prague
input bool              InpCloseForexOnFriday     = true;   // Fermer le Forex le vendredi à 20h00 UTC

input group "=== [4B] NEWS GUARD FTMO (CALENDRIER MT5 NATIF) ==="
input bool              InpEnableFTMONewsGuard       = true;   // Utilise le calendrier économique natif MT5
input bool              InpCloseBeforeRestrictedNews = true;   // Ferme préventivement les positions concernées
input int               InpPreNewsCloseMinutes       = 10;     // Notre marge de sécurité (pas une règle FTMO)
input bool              InpBlockRestrictedNews       = true;   // Verrouillage FTMO -2/+2 min
input bool              InpExportNewsCalendar        = true;   // Archive les événements restreints dans FILE_COMMON
input bool              InpUseNewsHistoryInTester    = true;   // Lit l'archive CSV en Strategy Tester
input int               InpNewsLookAheadHours        = 72;     // Horizon de lecture du calendrier MT5

input group "=== [5A] MARKET PROFILE & SIGNAL QUALITY ==="
input bool              InpEnableMarketProfile      = true;   // Détection automatique de classe/profil d'actif
input bool              InpEnableSignalRanking      = true;   // Classe et priorise les signaux simultanés
input double            InpScoreAPlus               = 90.0;
input double            InpScoreA                   = 80.0;
input double            InpScoreB                   = 70.0;
input double            InpQualityRiskAPlus         = 1.00;
input double            InpQualityRiskA             = 0.75;
input double            InpQualityRiskB             = 0.50;
input double            InpQualityRiskC             = 0.25;
input bool              InpAutoSetupTimeframe       = true; // profil marché par défaut
input ENUM_TIMEFRAMES   InpCryptoSetupTF            = PERIOD_M5;
input ENUM_TIMEFRAMES   InpClassicSetupTF           = PERIOD_M15;
input ENUM_TIMEFRAMES   InpCryptoMacroTF            = PERIOD_H1;
input ENUM_TIMEFRAMES   InpClassicMacroTF           = PERIOD_H1;
input double            InpCryptoLiquidMaxSpreadATR = 0.08; // profil liquide si spread <= 8% ATR (CFD)
input double            InpCryptoLiquidMinRelVolume = 0.50; // volume relatif minimal pour profil liquide
input bool              InpCryptoDirectionFilter     = true;   // Confirmation directionnelle supplémentaire pour le Momentum crypto
input int               InpCryptoDirectionEMA         = 50;     // EMA de direction court terme sur le TF setup crypto
input bool              InpCryptoRegimeEngine       = true;   // Moteur crypto multi-profils basé sur caractéristiques mesurables
input double            InpCryptoJumpATRRatio        = 2.40;   // V1: moins de faux SHOCK sur M5
input double            InpCryptoJumpCandleATR       = 2.00;   // V1
input double            InpCryptoPreShockATRRatio     = 1.80;   // V1
input double            InpCryptoPreShockCandleATR    = 1.60;   // V1
input double            InpCryptoExtensionATR         = 3.00;   // V1: 3.0 ATR (1.5 était trop restrictif M5)
input int               InpCryptoPostShockBars       = 2;      // V1: 2 barres M5
input int               InpDonchianPeriodCrypto    = 72;     // V1: 72 x M5 ~= 6h
input double            InpMinTradeRiskUSDCrypto  = 12.0;   // V1: min risk $ crypto
input bool              InpRequireVolumeSpikeCrypto = false; // V1: tick volume crypto OFF
input int               InpCryptoBTCContextEMA        = 50;     // EMA BTC M5 pour contexte altcoins
input double            InpCryptoAltBTCMinAlign       = 0.50;   // Alignement BTC minimal pour altcoins liquides
const int               InpBreakoutExitDonchian    = 10;
input double            InpStructuralSLBufferATR   = 0.25; // Buffer structurel en ATR; aucun plafond absolu de distance

input group "=== [5C] ELIGIBILITÉ PAR ACTIF/RÉGIME ==="
const bool              InpAllowBreakout           = false; // PROD D017: verrouille
const bool              InpAllowPullback           = false; // PROD D017: verrouille
const bool              InpAllowSweep              = false; // PROD D017: verrouille
const bool              InpAllowMomentum           = true;  // PROD D017: seul moteur autorise

input group "=== [5D] CRYPTO SWEEP / RECLAIM ==="
const bool              InpCryptoSweepEnabled      = false; // PROD D017: Sweep interdit
const ENUM_TIMEFRAMES   InpCryptoSweepTF           = PERIOD_M5;
const int               InpCryptoSweepLookback     = 24;
const double            InpCryptoSweepMinATR       = 1.20;
const double            InpCryptoSweepMaxATR       = 4.50;
const double            InpCryptoSweepMinWickPct   = 25.0;
const double            InpCryptoSweepReclaimPct   = 55.0;
const int               InpCryptoSweepConfirmBars  = 3;
const double            InpCryptoSweepSL_ATR       = 0.35;
const double            InpCryptoSweepTP_R         = 2.00;
const double            InpCryptoSweepRiskPct      = 0.25;

input group "=== [5] MOTEURS STRATÉGIQUES & FILTRES PRO ==="
const ENUM_STRATEGY_MODE InpStrategyMode          = MODE_PORTFOLIO_RANKED; // PROD D017: fige
input ENUM_TIMEFRAMES   InpMacroTrendTF           = PERIOD_H1;  // Tendance Macro (H1 Forex / H4 Crypto)
input int               InpMacroEMA_Period        = 200;    
input int               InpDonchianPeriod         = 24;     // Forex (sur setup TF). Crypto: voir InpDonchianPeriodCrypto
//--- Pullback structurel : impulsion -> retracement -> confirmation
const int               InpPullbackLookbackBars    = 6;      // Barres historiques utilisées pour identifier l'impulsion
const double            InpPullbackMinImpulseATR   = 0.80;   // Taille minimale de l'impulsion en ATR
const double            InpPullbackMinRetracePct   = 20.0;   // Retracement minimal de l'impulsion
const double            InpPullbackMaxRetracePct   = 70.0;   // Retracement maximal : évite les retournements déguisés
const double            InpPullbackMinConfirmBodyATR= 0.15;  // Corps minimal de la bougie de reprise
input int               InpATR_Period             = 14;     
input double            InpSL_ATR_Multiplier      = 1.60;   // Multiplicateur ATR pour Stop Loss
input int               InpADX_Period             = 14;     
input double            InpADX_TrendThreshold     = 20.0;   // Seuil de force de tendance
input bool              InpRequireVolumeSpike     = true;   // Exiger un pic de tick-volume relatif (1.25x, pas un flux institutionnel)
input double            InpMaxSpreadPercentOfSL   = 12.0;   // Filtre Spread max (% du SL)

input group "=== [5B] QUALITÉ MARCHÉ & EXPOSITION COMPTE ==="
input bool              InpEnableQualityFilter    = true;
input double            InpMinATRPercent          = 0.03;   // Évite les marchés trop plats
input double            InpMaxATRPercent          = 0.80;   // Bloque les chocs anormaux / annonces destructrices Forex
input double            InpMaxSignalCandleATR     = 1.50;   // Pas d'entrée après une bougie de choc

input group "=== [7] FILTRE DE SESSIONS / KILLZONES ==="
const bool              InpEnableSessionFilter    = true;  // PROD D017: fige   
const double            InpClassicSessionStartUTC = 7.0;   // PROD D017: fige
const double            InpClassicSessionEndUTC   = 17.0;  // PROD D017: fige
input bool              InpAllowCrypto247         = true;  // Crypto 24/7 (Forex reste filtré session)

input group "=== [8] GESTION PAR STRATÉGIE ==="
// Ces valeurs sont des hypothèses robustes de départ, à valider hors échantillon.
const double            InpBreakoutBE_R             = 1.50;
const double            InpBreakoutTrailATR         = 2.50;
const double            InpPullbackTP1_R            = 2.00;
const double            InpPullbackTP1_ClosePct     = 40.0;
const double            InpPullbackBE_R             = 1.25;
const double            InpPullbackTrailATR         = 2.00;
const double            InpSweepTP1_R               = 1.00;
const double            InpSweepTP1_ClosePct        = 50.0;
const double            InpSweepBE_R                 = 0.75;
const double            InpSweepTrailATR             = 1.00;
input double            InpMomentumTP1_R             = 2.00;
input double            InpMomentumTP1_ClosePct      = 25.0;
input double            InpMomentumBE_R              = 1.25;
input double            InpMomentumTrailATR          = 1.75;
const int               InpBreakoutMaxMinutes        = 240;
const int               InpPullbackMaxMinutes        = 45;
const int               InpSweepMaxMinutes           = 30;
const int               InpMomentumMaxMinutes        = 60; // legacy inactif: time-stop OFF
const double            InpBreakoutMinProgressR      = 0.30;
const double            InpPullbackMinProgressR      = 0.30;
const double            InpSweepMinProgressR         = 0.50;
const double            InpMomentumMinProgressR      = 0.40; // legacy inactif: time-stop OFF
const bool              InpEnableStrategyTimeStop   = false; // PROD D017: VERROUILLE OFF

input group "=== [9] BLACKBOX & LEDGER (AUCUNE MESSAGERIE EXTERNE) ==="
input bool              InpEnableBlackBox         = false;  // diagnostic optionnel; OFF par defaut PROD
input bool              InpEnableUnifiedLedger    = false;  // diagnostic optionnel; OFF par defaut PROD

//--- PROP FIRM RUNTIME BADGE (issu de PropFirmGuard v0.4)
#define PFG_BADGE "PFG_RISK_REVIEW_BADGE"
#define PFG_OWNER_STALE_SEC 90
class CPfgRiskBadge
  {
private:
   bool   m_owner;
   string m_owner_gv;
   string m_hb_gv;
   string m_lastlog_gv;
   double m_token;

   bool ClaimOwner()
     {
      double now=(double)TimeLocal();
      if(!GlobalVariableCheck(m_owner_gv)) GlobalVariableSet(m_owner_gv,0.0);
      if(!GlobalVariableCheck(m_hb_gv)) GlobalVariableSet(m_hb_gv,0.0);
      double cur=GlobalVariableGet(m_owner_gv);
      if(cur==m_token)
        {
         m_owner=true;
         GlobalVariableSet(m_hb_gv,now);
         return true;
        }
      if(cur==0.0 && GlobalVariableSetOnCondition(m_owner_gv,m_token,0.0))
        {
         m_owner=true;
         GlobalVariableSet(m_hb_gv,now);
         return true;
        }
      double hb=GlobalVariableGet(m_hb_gv);
      if((now-hb)>PFG_OWNER_STALE_SEC)
        {
         cur=GlobalVariableGet(m_owner_gv);
         if(GlobalVariableSetOnCondition(m_owner_gv,m_token,cur))
           {
            m_owner=true;
            GlobalVariableSet(m_hb_gv,now);
            return true;
           }
        }
      m_owner=false;
      return false;
     }

   void SetAllCharts(bool active,int count)
     {
      long id=ChartFirst();
      while(id>=0)
        {
         if(active)
           {
            if(ObjectFind(id,PFG_BADGE)<0)
              {
               if(ObjectCreate(id,PFG_BADGE,OBJ_LABEL,0,0,0))
                 {
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_CORNER,CORNER_RIGHT_UPPER);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_XDISTANCE,18);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_YDISTANCE,18);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_FONTSIZE,14);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_COLOR,clrRed);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_SELECTABLE,false);
                  ObjectSetInteger(id,PFG_BADGE,OBJPROP_HIDDEN,false);
                 }
              }
            string t="● REVUE";
            if(count>1) t+=" ("+IntegerToString(count)+")";
            ObjectSetString(id,PFG_BADGE,OBJPROP_TEXT,t);
           }
         else if(ObjectFind(id,PFG_BADGE)>=0) ObjectDelete(id,PFG_BADGE);
         ChartRedraw(id);
         id=ChartNext(id);
        }
     }
public:
   CPfgRiskBadge(){m_owner=false;m_token=0.0;}
   bool Init(string terminal_key)
     {
      m_owner_gv="PFG4_"+terminal_key+"_OWNER";
      m_hb_gv="PFG4_"+terminal_key+"_HB";
      m_lastlog_gv="PFG4_"+terminal_key+"_LOG";
      m_token=(double)ChartID()+1.0;
      return ClaimOwner();
     }
   void Pulse(){if(ClaimOwner()) GlobalVariableSet(m_hb_gv,(double)TimeLocal());}
   void ApplyRiskState(int count,string summary)
     {
      if(!ClaimOwner()) return;
      SetAllCharts(count>0,count);
      if(count<=0) return;
      double now=(double)TimeLocal();
      if(!GlobalVariableCheck(m_lastlog_gv) || now-GlobalVariableGet(m_lastlog_gv)>=600.0)
        {
         Print("[PROPFIRM RISK REVIEW] ",summary);
         GlobalVariableSet(m_lastlog_gv,now);
        }
     }
   void Release()
     {
      if(!m_owner) return;
      if(GlobalVariableCheck(m_owner_gv)) GlobalVariableSetOnCondition(m_owner_gv,0.0,m_token);
      m_owner=false;
     }
  };

//--- STRUCTURES GLOBALES
struct GuardianSnapshot
  {
   double balance;
   double equity;
   double daily_start_reference;
   double daily_loss;
   double daily_profit;
   double daily_remaining_guardian;
   double overall_remaining_guardian;
   double open_risk_usd;
   int    total_account_positions;
   int    symbol_positions;
   ENUM_FTMO_GUARDIAN_STATE state;
  };

//--- VARIABLES GLOBALES
CTrade                  g_trade;
GuardianSnapshot        g_snap;
ulong                   g_auto_magic          = 0;
int                     g_atr_handle          = INVALID_HANDLE;
int                     g_adx_handle          = INVALID_HANDLE;
int                     g_macro_ema_handle    = INVALID_HANDLE; 
datetime                g_last_signal_bar_time = 0;
datetime                g_account_cooldown_to = 0;
int                     g_consecutive_losses  = 0;
double                  g_detected_base_cap   = 10000.0;
datetime                g_last_force_close_att = 0;
datetime                g_trade_session_backoff_until = 0;
bool                    g_emergency_event_announced = false;
ulong                   g_pending_closed_positions[64];
int                     g_pending_closed_count = 0;
int                     g_crypto_trades_today = 0;
datetime                g_crypto_trades_day_key = 0;
datetime                g_crypto_cooldown_to = 0;

//--- v11.15 : ownership multi-instance
long                    g_instance_chart_id = 0;
bool                    g_manual_guard_owner = true;
bool                    g_symbol_instance_owner = true;
datetime                g_last_owner_refresh_local = 0;
string                  g_manual_candidate_key = "";
string                  g_symbol_candidate_key = "";

//--- MARKET PROFILE / SIGNAL QUALITY
ENUM_MARKET_CLASS g_market_class = MARKET_OTHER;
ENUM_MARKET_REGIME g_market_regime = REGIME_UNKNOWN;
string g_market_profile_name = "OTHER";
double g_relative_atr_ratio = 1.0;
double g_selected_signal_risk_factor = 1.0;

//--- NEWS GUARD
struct NewsEventCache
  {
   datetime time;
   ulong    event_id;
   string   currency;
   string   name;
  };
NewsEventCache g_news_cache[];
datetime g_news_cache_last_refresh = 0;
datetime g_news_last_action_time = 0;
datetime g_ea_start_time = 0;
ulong g_news_last_action_event = 0;
string g_news_csv_name = "";
string g_ledger_csv_name = "";

//--- PROP FIRM CONTEXT
ENUM_PROP_FIRM       g_prop_firm=PROP_FIRM_OTHER;
ENUM_PROP_PROFILE    g_prop_profile=PFG_PROFILE_UNKNOWN;
ENUM_PROP_NEWS_POLICY g_prop_news_policy=PROP_NEWS_NONE;
string g_prop_detection_reason="";
string g_prop_server="";
string g_prop_company="";
string g_prop_account_name="";
string g_prop_profile_routing_source="account text";
bool   g_prop_execution_authorized=true;
string g_prop_execution_reason="";
CPfgRiskBadge g_pfg_badge;
int      g_pfg_runtime_alert_count=0;
bool     g_pfg_runtime_stale=false;
string   g_pfg_runtime_summary="";
datetime g_pfg_last_runtime_scan=0;
bool     g_pfg_rules_loaded=false;
double   g_pfg_runtime_daily_loss_pct=-1.0;
double   g_pfg_runtime_overall_loss_pct=-1.0;
bool     g_pfg_runtime_ea_allowed_known=false;
bool     g_pfg_runtime_ea_allowed=true;
string   g_pfg_runtime_news_rule="";

//+------------------------------------------------------------------+
//| UTILS TEMPS & DÉTECTION DU CAPITAL INITIAL                       |
//+------------------------------------------------------------------+
ulong GenerateAutoMagicNumber(string symbol)
  {
   ulong hash = 5381;
   for(int i = 0; i < StringLen(symbol); i++)
      hash = ((hash << 5) + hash) + StringGetCharacter(symbol, i);
   return (hash % 799999) + 100000;
  }

double DetectInitialCapitalRobust()
  {
   if(InpInitialCapitalOverride > 0.0) return InpInitialCapitalOverride;

   // Les comptes FTMO usuels sont arrondis à cette grille. On prend la taille
   // la plus proche de la balance/equity actuelle, y compris après un gain/perte.
   double reference = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));
   if(reference <= 0.0) return 10000.0;

   double known_sizes[] = {6000.0, 10000.0, 15000.0, 25000.0, 50000.0, 100000.0, 200000.0};
   double selected = known_sizes[0];
   double best_distance = 1.0e100;
   for(int i = 0; i < ArraySize(known_sizes); i++)
     {
      double distance = MathAbs(reference - known_sizes[i]);
      if(distance < best_distance)
        {
         best_distance = distance;
         selected = known_sizes[i];
        }
     }
   return selected;
  }

int LastSundayOfMonth(int year, int month)
  {
   MqlDateTime dt;
   dt.year = year; dt.mon = month; dt.day = 1; dt.hour = 12; dt.min = 0; dt.sec = 0;
   datetime first = StructToTime(dt);
   int next_year = (month == 12 ? year + 1 : year);
   int next_month = (month == 12 ? 1 : month + 1);
   dt.year = next_year; dt.mon = next_month; dt.day = 1; dt.hour = 12; dt.min = 0; dt.sec = 0;
   datetime next = StructToTime(dt);
   datetime last = next - 86400;
   TimeToStruct(last, dt);
   return dt.day - dt.day_of_week;
  }

bool IsPragueSummerTime(datetime utc_time)
  {
   MqlDateTime dt;
   TimeToStruct(utc_time, dt);
   if(dt.mon < 3 || dt.mon > 10) return false;
   if(dt.mon > 3 && dt.mon < 10) return true;
   int sunday = LastSundayOfMonth(dt.year, dt.mon);
   if(dt.mon == 3)
     {
      if(dt.day > sunday) return true;
      if(dt.day < sunday) return false;
      return dt.hour >= 1;
     }
   if(dt.day < sunday) return true;
   if(dt.day > sunday) return false;
   return dt.hour < 1;
  }

datetime PragueNow()
  {
   datetime utc = (MQLInfoInteger(MQL_TESTER) ? TimeCurrent() : TimeGMT());
   int offset_hours = IsPragueSummerTime(utc) ? 2 : 1;
   return utc + (offset_hours * 3600);
  }

datetime PragueDayKey(datetime prague_time)
  {
   MqlDateTime dt;
   TimeToStruct(prague_time, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
  }

string PfgUpper(string s){StringToUpper(s); return s;}
bool PfgContains(string s,string needle){return StringFind(PfgUpper(s),PfgUpper(needle))>=0;}

string PropFirmName(ENUM_PROP_FIRM f)
  {
   if(f==PROP_FIRM_FTMO) return "FTMO";
   if(f==PROP_FIRM_FUNDEDNEXT) return "FUNDEDNEXT";
   if(f==PROP_FIRM_THE5ERS) return "THE5ERS";
   if(f==PROP_FIRM_AUTO) return "AUTO";
   return "OTHER";
  }

string PropProfileName(ENUM_PROP_PROFILE p)
  {
   if(p==PFG_FTMO_2STEP_CHALLENGE) return "FTMO_2STEP_CHALLENGE";
   if(p==PFG_FTMO_2STEP_FUNDED_STANDARD) return "FTMO_2STEP_FUNDED_STANDARD";
   if(p==PFG_FTMO_2STEP_FUNDED_SWING) return "FTMO_2STEP_FUNDED_SWING";
   if(p==PFG_FUNDEDNEXT_FREE_TRIAL) return "FUNDEDNEXT_FREE_TRIAL";
   if(p==PFG_FUNDEDNEXT_STELLAR_2STEP_CHALLENGE) return "FUNDEDNEXT_STELLAR_2STEP_CHALLENGE";
   if(p==PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED) return "FUNDEDNEXT_STELLAR_2STEP_FUNDED";
   if(p==PFG_THE5ERS_HIGH_STAKES) return "THE5ERS_HIGH_STAKES";
   if(p==PFG_PROFILE_AUTO) return "AUTO";
   return "UNKNOWN";
  }

ENUM_PROP_PROFILE PropProfileFromName(string name)
  {
   if(name=="FTMO_2STEP_CHALLENGE") return PFG_FTMO_2STEP_CHALLENGE;
   if(name=="FTMO_2STEP_FUNDED_STANDARD") return PFG_FTMO_2STEP_FUNDED_STANDARD;
   if(name=="FTMO_2STEP_FUNDED_SWING") return PFG_FTMO_2STEP_FUNDED_SWING;
   if(name=="FUNDEDNEXT_FREE_TRIAL") return PFG_FUNDEDNEXT_FREE_TRIAL;
   if(name=="FUNDEDNEXT_STELLAR_2STEP_CHALLENGE") return PFG_FUNDEDNEXT_STELLAR_2STEP_CHALLENGE;
   if(name=="FUNDEDNEXT_STELLAR_2STEP_FUNDED") return PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED;
   if(name=="THE5ERS_HIGH_STAKES") return PFG_THE5ERS_HIGH_STAKES;
   return PFG_PROFILE_UNKNOWN;
  }

ENUM_PROP_PROFILE DetectPropProfileFromRuntimeMap(ENUM_PROP_FIRM firm)
  {
   string txt=PfgReadCommonText("profiles.json");
   if(txt=="") return PFG_PROFILE_UNKNOWN;
   string wanted_firm=PropFirmName(firm);
   string login=StringFormat("%I64d",AccountInfoInteger(ACCOUNT_LOGIN));
   int pos=0;
   while(true)
     {
      int pidpos=StringFind(txt,"\"profile_id\"",pos); if(pidpos<0) break;
      int a=pidpos; while(a>0 && StringGetCharacter(txt,a)!='{') a--;
      int b=StringFind(txt,"}",pidpos); if(b<0) break;
      string obj=StringSubstr(txt,a,b-a+1); pos=b+1;
      if(PfgJsonString(obj,"prop_firm_id")!=wanted_firm) continue;
      if(!PfgArrayContains(obj,"account_logins",login)) continue;
      ENUM_PROP_PROFILE mapped=PropProfileFromName(PfgJsonString(obj,"profile_id"));
      if(mapped!=PFG_PROFILE_UNKNOWN)
        {
         g_prop_profile_routing_source="profiles.json/account_login";
         return mapped;
        }
     }
   return PFG_PROFILE_UNKNOWN;
  }

string PropNewsPolicyName()
  {
   if(g_prop_news_policy==PROP_NEWS_FTMO_RESTRICTED_2MIN) return "FTMO -2/+2 min";
   if(g_prop_news_policy==PROP_NEWS_FUNDEDNEXT_REWARD_5MIN) return "FN funded +/-5 min (40% profit)";
   return "LIBRE / AUCUN BLOCAGE";
  }

ENUM_PROP_FIRM DetectPropFirmFromAccount()
  {
   g_prop_server=AccountInfoString(ACCOUNT_SERVER);
   g_prop_company=AccountInfoString(ACCOUNT_COMPANY);
   g_prop_account_name=AccountInfoString(ACCOUNT_NAME);
   string all=PfgUpper(g_prop_server+" "+g_prop_company+" "+g_prop_account_name);
   if(StringFind(all,"FUNDEDNEXT")>=0 || StringFind(all,"FUNDED NEXT")>=0) return PROP_FIRM_FUNDEDNEXT;
   if(StringFind(all,"FTMO")>=0) return PROP_FIRM_FTMO;
   if(StringFind(all,"THE5ERS")>=0 || StringFind(all,"5ERS")>=0) return PROP_FIRM_THE5ERS;
   return PROP_FIRM_OTHER;
  }

ENUM_PROP_PROFILE DetectPropProfileFromAccount(ENUM_PROP_FIRM firm)
  {
   ENUM_PROP_PROFILE mapped=DetectPropProfileFromRuntimeMap(firm);
   if(mapped!=PFG_PROFILE_UNKNOWN) return mapped;
   g_prop_profile_routing_source="account text";
   string all=PfgUpper(g_prop_server+" "+g_prop_company+" "+g_prop_account_name);
   if(firm==PROP_FIRM_FUNDEDNEXT)
     {
      if(StringFind(all,"FREE TRIAL")>=0 || StringFind(all,"FREETRIAL")>=0 || StringFind(all,"TRIAL")>=0)
         return PFG_FUNDEDNEXT_FREE_TRIAL;
      if(StringFind(all,"STELLAR")>=0 || StringFind(all,"2-STEP")>=0 || StringFind(all,"2 STEP")>=0 || StringFind(all,"2STEP")>=0)
        {
         if(StringFind(all,"FUNDED")>=0 && StringFind(all,"CHALLENGE")<0) return PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED;
         return PFG_FUNDEDNEXT_STELLAR_2STEP_CHALLENGE;
        }
      return PFG_PROFILE_UNKNOWN;
     }
   if(firm==PROP_FIRM_FTMO)
     {
      if(StringFind(all,"SWING")>=0) return PFG_FTMO_2STEP_FUNDED_SWING;
      if(StringFind(all,"FUNDED")>=0 || StringFind(all,"FTMO ACCOUNT")>=0) return PFG_FTMO_2STEP_FUNDED_STANDARD;
      return PFG_FTMO_2STEP_CHALLENGE;
     }
   if(firm==PROP_FIRM_THE5ERS) return PFG_THE5ERS_HIGH_STAKES;
   return PFG_PROFILE_UNKNOWN;
  }

void ResolvePropFirmContext()
  {
   ENUM_PROP_FIRM detected=(InpEnablePropFirmAutoDetect ? DetectPropFirmFromAccount() : PROP_FIRM_OTHER);
   if(!InpEnablePropFirmAutoDetect) DetectPropFirmFromAccount(); // remplit seulement les champs HUD
   g_prop_firm=(InpPropFirmOverride!=PROP_FIRM_AUTO ? InpPropFirmOverride : detected);
   g_prop_profile=(InpPropProfileOverride!=PFG_PROFILE_AUTO ? InpPropProfileOverride : DetectPropProfileFromAccount(g_prop_firm));
   if(InpPropProfileOverride!=PFG_PROFILE_AUTO) g_prop_profile_routing_source="input override";
   if(MQLInfoInteger(MQL_TESTER) && g_prop_profile==PFG_PROFILE_UNKNOWN)
     {
      g_prop_firm=PROP_FIRM_FTMO;
      g_prop_profile=PFG_FTMO_2STEP_CHALLENGE;
     }
   g_prop_detection_reason=StringFormat("server=%s | company=%s | name=%s | route=%s",g_prop_server,g_prop_company,g_prop_account_name,g_prop_profile_routing_source);

   g_prop_news_policy=PROP_NEWS_NONE;
   if(g_prop_profile==PFG_FTMO_2STEP_FUNDED_STANDARD) g_prop_news_policy=PROP_NEWS_FTMO_RESTRICTED_2MIN;
   if(g_prop_profile==PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED) g_prop_news_policy=PROP_NEWS_FUNDEDNEXT_REWARD_5MIN;

   g_prop_execution_authorized=true; g_prop_execution_reason="OK";
   if(InpBlockAutoTradingOnUnknownPropProfile && g_prop_profile==PFG_PROFILE_UNKNOWN)
     {g_prop_execution_authorized=false; g_prop_execution_reason="PROFIL PROP FIRM NON RESOLU";}
   if(g_prop_profile==PFG_FUNDEDNEXT_FREE_TRIAL)
     {g_prop_execution_authorized=false; g_prop_execution_reason="FUNDEDNEXT FREE TRIAL: EA INTERDIT";}
   else if(g_prop_firm==PROP_FIRM_FUNDEDNEXT && !InpFundedNextEAUsageAuthorized)
     {g_prop_execution_authorized=false; g_prop_execution_reason="FUNDEDNEXT: AUTORISATION/OPTION EA NON CONFIRMEE";}
  }

bool IsTightOneStepProfile()
  {
   // Seul le FTMO 1-Step historique utilise ici le resserrement 3%/trailing EOD.
   return (g_prop_firm==PROP_FIRM_FTMO && g_prop_profile==PFG_PROFILE_UNKNOWN && InpFTMORuleSet==FTMO_RULESET_1_STEP);
  }

double ComplianceDailyLossPct()
  {
   if(g_pfg_runtime_daily_loss_pct>0.0) return g_pfg_runtime_daily_loss_pct;
   if(g_prop_profile==PFG_FUNDEDNEXT_FREE_TRIAL || g_prop_profile==PFG_FUNDEDNEXT_STELLAR_2STEP_CHALLENGE || g_prop_profile==PFG_FUNDEDNEXT_STELLAR_2STEP_FUNDED) return 5.0;
   if(g_prop_profile==PFG_FTMO_2STEP_CHALLENGE || g_prop_profile==PFG_FTMO_2STEP_FUNDED_STANDARD || g_prop_profile==PFG_FTMO_2STEP_FUNDED_SWING) return 5.0;
   if(g_prop_profile==PFG_THE5ERS_HIGH_STAKES) return 5.0;
   return (InpFTMORuleSet==FTMO_RULESET_1_STEP ? 3.0 : InpFTMODailyLossPct);
  }

double ComplianceOverallLossPct()
  {
   if(g_pfg_runtime_overall_loss_pct>0.0) return g_pfg_runtime_overall_loss_pct;
   if(g_prop_profile!=PFG_PROFILE_UNKNOWN) return 10.0;
   return InpFTMOOverallLossPct;
  }

bool ComplianceOverallTrailingEOD(){return IsTightOneStepProfile();}

datetime ServerDayKey(datetime server_time)
  {
   MqlDateTime dt; TimeToStruct(server_time,dt); dt.hour=0; dt.min=0; dt.sec=0; return StructToTime(dt);
  }

bool ComplianceUsesServerMidnight()
  {
   return (g_prop_firm==PROP_FIRM_FUNDEDNEXT || g_prop_firm==PROP_FIRM_THE5ERS);
  }

datetime ComplianceDayKeyNow()
  {
   if(ComplianceUsesServerMidnight())
     {datetime t=TimeTradeServer(); if(t<=0) t=TimeCurrent(); return ServerDayKey(t);}
   return PragueDayKey(PragueNow());
  }

bool IsComplianceRolloverWindow()
  {
   if(!InpProtectRolloverWindow) return false;
   datetime t=(ComplianceUsesServerMidnight() ? TimeTradeServer() : PragueNow());
   if(t<=0) t=TimeCurrent();
   MqlDateTime dt; TimeToStruct(t,dt);
   return ((dt.hour==23 && dt.min>=45) || (dt.hour==0 && dt.min<=15));
  }

bool IsPragueRolloverWindow()
  {
   if(!InpProtectRolloverWindow) return false;
   MqlDateTime dt;
   TimeToStruct(PragueNow(), dt);
   if((dt.hour == 23 && dt.min >= 45) || (dt.hour == 0 && dt.min <= 15)) return true;
   return false;
  }

bool IsSymbolCrypto(string symbol)
  {
   string upper=symbol; StringToUpper(upper);
   string crypto_tokens[] = {
      "BTC","ETH","SOL","XRP","ADA","DOGE","LTC","BCH","XLM","UNI",
      "DOT","AVAX","LINK","MATIC","ATOM","NEAR","APT","ARB","OP",
      "FIL","ETC","ALGO","SUI","HBAR","PEPE","SHIB","CRYPTO"
   };
   for(int i=0;i<ArraySize(crypto_tokens);i++)
      if(StringFind(upper,crypto_tokens[i])>=0) return true;
   return false;
  }

bool IsExposureCode(string code)
  {
   return (code == "USD" || code == "EUR" || code == "GBP" || code == "JPY" ||
           code == "CHF" || code == "CAD" || code == "AUD" || code == "NZD" ||
           code == "XAU" || code == "XAG" || code == "BTC" || code == "ETH" ||
           code == "SOL" || code == "XRP");
  }

bool GetSymbolCurrencies(string symbol, string &base_currency, string &quote_currency)
  {
   // Garde uniquement les lettres afin d'accepter les suffixes broker (EURUSD.a, XAUUSDm, etc.).
   string letters = "";
   for(int i = 0; i < StringLen(symbol); i++)
     {
      ushort c = StringGetCharacter(symbol, i);
      if((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) letters += CharToString((uchar)c);
     }
   StringToUpper(letters);
   if(StringLen(letters) < 6) return false;
   base_currency = StringSubstr(letters, 0, 3);
   quote_currency = StringSubstr(letters, 3, 3);
   return (IsExposureCode(base_currency) && IsExposureCode(quote_currency));
  }

bool IsInAllowedSession()
  {
   datetime gmt = (MQLInfoInteger(MQL_TESTER) ? TimeCurrent() : TimeGMT());
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   double h = dt.hour + (dt.min / 60.0);
   string cls = LedgerAssetClass(_Symbol);

   // Crypto : 24/7. Le rollover reste bloque separement par GuardianEvaluateOpen().
   if(cls == "CRYPTO")
     {
      if(InpAllowCrypto247) return true;
      return false;
     }

   // Week-end / debuts de semaine : le broker determine aussi la negociabilite reelle.
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;
   // Ne jamais rouvrir juste après la clôture préventive du vendredi.
   if(InpCloseForexOnFriday && dt.day_of_week == 5 && h >= 20.0) return false;

   if(!InpEnableSessionFilter) return true;

   // D-015 : fenêtre paramétrée et préenregistrée ; elle ne concerne que les nouvelles entrées.
   double session_start=InpClassicSessionStartUTC;
   double session_end=InpClassicSessionEndUTC;
   bool in_classic_session=(session_start<session_end ?
                            (h>=session_start && h<session_end) :
                            (h>=session_start || h<session_end));
   if(cls == "FOREX" || cls == "GOLD" || cls == "SILVER" || cls == "OIL" || cls == "INDEX")
      return in_classic_session;
   return in_classic_session;
  }

// Vérifie le calendrier de négociation déclaré par le symbole. Si aucun
// calendrier n'est disponible, known=false et l'appelant conserve le
// comportement existant afin de ne pas neutraliser la gestion du risque.
bool IsDeclaredTradeSessionOpen(string symbol,datetime now,bool &known)
  {
   known=false;
   if(now<=0) now=TimeTradeServer();
   if(now<=0) now=TimeCurrent();
   MqlDateTime dt;
   if(!TimeToStruct(now,dt)) return true;
   ulong seconds_now=(ulong)(dt.hour*3600+dt.min*60+dt.sec);
   datetime session_from=0,session_to=0;
   for(uint index=0;index<32;index++)
     {
      if(!SymbolInfoSessionTrade(symbol,(ENUM_DAY_OF_WEEK)dt.day_of_week,index,session_from,session_to)) break;
      known=true;
      ulong from_seconds=(ulong)session_from;
      ulong to_seconds=(ulong)session_to;
      if(to_seconds==from_seconds) return true; // session de 24 heures
      if(to_seconds>from_seconds)
        {
         if(seconds_now>=from_seconds && seconds_now<to_seconds) return true;
        }
      else if(seconds_now>=from_seconds || seconds_now<to_seconds)
         return true; // session traversant minuit
     }
   return !known;
  }

// Certains symboles personnalisés ne publient pas de calendrier de séance.
// Le refus 10018 du serveur devient alors le signal d'indisponibilité et
// suspend les nouvelles requêtes jusqu'à la bougie de décision suivante.
bool RegisterMarketClosedBackoff(string action,ulong ticket=0)
  {
   if(g_trade.ResultRetcode()!=TRADE_RETCODE_MARKET_CLOSED) return false;
   int retry_seconds=PeriodSeconds(GetProfileSetupTF("PORTFOLIO"));
   if(retry_seconds<60) retry_seconds=60;
   datetime candidate=TimeCurrent()+retry_seconds;
   if(candidate>g_trade_session_backoff_until) g_trade_session_backoff_until=candidate;
   if(InpEnableExitDebugLogs)
      PrintFormat("[EXIT_DIAG] market closed backoff | ticket=%I64u action=%s retryAfter=%s",ticket,action,TimeToString(g_trade_session_backoff_until,TIME_DATE|TIME_MINUTES));
   return true;
  }

bool IsMarketClosedBackoffActive()
  {
   return (g_trade_session_backoff_until>0 && TimeCurrent()<g_trade_session_backoff_until);
  }

bool AcquireGlobalTradeLock(int timeout_seconds = 3)
  {
   if(MQLInfoInteger(MQL_TESTER)) return true;
   string lock_key = StringFormat("FTMO_PRO_MUTEX_%I64d", AccountInfoInteger(ACCOUNT_LOGIN));
   datetime now = TimeCurrent();
   
   for(int i = 0; i < 10; i++)
     {
      if(GlobalVariableSetOnCondition(lock_key, (double)(now + timeout_seconds), 0.0))
         return true;
      if(GlobalVariableCheck(lock_key))
        {
         double expire = GlobalVariableGet(lock_key);
         if((datetime)expire < now)
           {
            if(GlobalVariableSetOnCondition(lock_key, (double)(now + timeout_seconds), expire))
               return true;
           }
        }
      else
        {
         GlobalVariableSet(lock_key, (double)(now + timeout_seconds));
         return true;
        }
      Sleep(25);
     }
   return false;
  }

void ReleaseGlobalTradeLock()
  {
   if(MQLInfoInteger(MQL_TESTER)) return;
   string lock_key = StringFormat("FTMO_PRO_MUTEX_%I64d", AccountInfoInteger(ACCOUNT_LOGIN));
   if(GlobalVariableCheck(lock_key)) GlobalVariableSet(lock_key, 0.0);
  }

//--- REGISTRE D'ÉTAT DES POSITIONS
string TicketGV(string prefix, ulong ticket) 
  { 
   return StringFormat("FTMO_PRO_V9_%I64u_%s", ticket, prefix); 
  }

void SetTicketState(string prefix, ulong ticket, double value) 
  { 
   GlobalVariableSet(TicketGV(prefix, ticket), value); 
  }

bool GetTicketState(string prefix, ulong ticket, double &value) 
  { 
   string key = TicketGV(prefix, ticket); 
   if(GlobalVariableCheck(key)) 
     { 
      value = GlobalVariableGet(key); 
      return true; 
     } 
   return false; 
  }

void DeleteTicketState(ulong ticket) 
  { 
   GlobalVariableDel(TicketGV("RISK", ticket)); 
   GlobalVariableDel(TicketGV("RISKUSD", ticket)); 
   GlobalVariableDel(TicketGV("TP1", ticket));
   GlobalVariableDel(TicketGV("BE", ticket));
   GlobalVariableDel(TicketGV("MTP", ticket)); 
   GlobalVariableDel(TicketGV("MFE", ticket)); 
   GlobalVariableDel(TicketGV("MAE", ticket));
   GlobalVariableDel(TicketGV("STRAT", ticket));
   GlobalVariableDel(TicketGV("TRAILBAR", ticket));
   GlobalVariableDel(TicketGV("MANUAL", ticket));
   GlobalVariableDel(TicketGV("DETECTMS", ticket));
  }

string AccountStateGV(string field)
  {
   return StringFormat("FTMO_PRO_V970_ACCOUNT_%I64d_%s", AccountInfoInteger(ACCOUNT_LOGIN), field);
  }

datetime GetAccountCooldownUntil()
  {
   string key = AccountStateGV("COOLDOWN");
   return (GlobalVariableCheck(key) ? (datetime)GlobalVariableGet(key) : 0);
  }

int GetAccountConsecutiveLosses()
  {
   string key = AccountStateGV("LOSSES");
   return (GlobalVariableCheck(key) ? (int)GlobalVariableGet(key) : 0);
  }

void SetAccountCooldownState(int losses, datetime cooldown_until)
  {
   GlobalVariableSet(AccountStateGV("LOSSES"), losses);
   GlobalVariableSet(AccountStateGV("COOLDOWN"), (double)cooldown_until);
   g_consecutive_losses = losses;
   g_account_cooldown_to = cooldown_until;
  }

//+------------------------------------------------------------------+
//| LIMITES CRYPTO / COOLDOWN (Forex inchangé si inputs à 0)           |
//+------------------------------------------------------------------+
int CountSymbolPositionsDir(string symbol, ENUM_ORDER_TYPE order_type)
  {
   int count = 0;
   long want = (order_type == ORDER_TYPE_BUY ? POSITION_TYPE_BUY : POSITION_TYPE_SELL);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
      if(PositionGetInteger(POSITION_TYPE) == want) count++;
     }
   return count;
  }

int CountCryptoAccountPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(IsSymbolCrypto(sym)) count++;
     }
   return count;
  }

void RefreshCryptoDailyTradeCounter()
  {
   datetime day = PragueDayKey(PragueNow());
   if(g_crypto_trades_day_key != day)
     {
      g_crypto_trades_day_key = day;
      g_crypto_trades_today = 0;
     }
  }

void RegisterCryptoTradeOpened()
  {
   RefreshCryptoDailyTradeCounter();
   g_crypto_trades_today++;
  }

datetime CooldownSecondsForLossStreak(int losses)
  {
   if(InpConsecutiveLossCooldown <= 0 || losses < InpConsecutiveLossCooldown) return 0;
   // Escalade légère type desk risk: 3→base, 6→1.5x, 9→2x
   double mult = 1.0;
   if(losses >= InpConsecutiveLossCooldown * 3) mult = 2.0;
   else if(losses >= InpConsecutiveLossCooldown * 2) mult = 1.5;
   int mins = InpCooldownMinutesAfterLosses;
   if(g_market_class == MARKET_CRYPTO && InpCryptoCooldownMinutes > mins)
      mins = InpCryptoCooldownMinutes;
   return (datetime)(mins * 60 * mult);
  }



//+------------------------------------------------------------------+
//| BLACKBOX & TELEGRAM                                              |
//+------------------------------------------------------------------+
string CsvSafe(string value)
  {
   StringReplace(value, ";", ",");
   StringReplace(value, "\r", " ");
   StringReplace(value, "\n", " ");
   return value;
  }

string LedgerAssetClass(string symbol)
  {
   string u=symbol; StringToUpper(u);
   if(IsSymbolCrypto(u)) return "CRYPTO";
   if(StringFind(u,"XAU")>=0 || StringFind(u,"GOLD")>=0) return "GOLD";
   if(StringFind(u,"XAG")>=0 || StringFind(u,"SILVER")>=0) return "SILVER";
   if(StringFind(u,"WTI")>=0 || StringFind(u,"USOIL")>=0 || StringFind(u,"XTI")>=0 || StringFind(u,"UKOIL")>=0 || StringFind(u,"BRENT")>=0) return "OIL";
   if(StringFind(u,"US30")>=0 || StringFind(u,"DJI")>=0 || StringFind(u,"NAS")>=0 || StringFind(u,"USTEC")>=0 || StringFind(u,"SPX")>=0 || StringFind(u,"US500")>=0 || StringFind(u,"GER")>=0 || StringFind(u,"DE40")>=0 || StringFind(u,"FRA40")>=0 || StringFind(u,"UK100")>=0) return "INDEX";
   string b,q; if(GetSymbolCurrencies(u,b,q)) return "FOREX";
   return "OTHER";
  }

// Session/horaire sont calcules sur l'heure serveur du deal pour rester reproductibles dans l'historique MT5.
string LedgerSession(datetime t, string symbol)
  {
   datetime utc=t;
   MqlDateTime dt; TimeToStruct(utc,dt);
   double h=dt.hour+dt.min/60.0;
   string cls=LedgerAssetClass(symbol);
   if(cls=="CRYPTO")
     {
      if(dt.day_of_week==0 || dt.day_of_week==6) return "WEEKEND";
     }
   if(h>=8.0 && h<13.5) return "LONDON";
   if(h>=13.5 && h<16.5) return "LONDON_NY_OVERLAP";
   if(h>=16.5 && h<21.0) return "NEW_YORK";
   return "ASIA_OTHER";
  }

string LedgerSource(string event_name, string details)
  {
   if(StringFind(event_name,"MANUAL_")==0) return "MANUAL";
   if(StringFind(event_name,"AUTO_")==0 || StringFind(event_name,"ORDER_")==0) return "AUTO";
   if(StringFind(event_name,"TRADE_CLOSED")==0) return "TRADE";
   if(StringFind(details,"MANUAL")>=0 || StringFind(details,"manuel")>=0) return "MANUAL";
   return "GUARDIAN";
  }

void UnifiedLedgerEvent(string event_name, string details, string symbol="", string strategy="", string source="")
  {
   if(!InpEnableUnifiedLedger) return;
   if(symbol=="") symbol=_Symbol;
   if(source=="") source=LedgerSource(event_name,details);
   datetime now=TimeCurrent();
   string file_name=StringFormat("Guardian_Ledger_%I64d.csv",AccountInfoInteger(ACCOUNT_LOGIN));
   int file=FileOpen(file_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,';');
   if(file==INVALID_HANDLE) return;
   if(FileSize(file)==0)
      FileWrite(file,"timestamp_server","timestamp_prague","account","event","source","symbol","asset_class","strategy","timeframe","session","hour_server","balance","equity","risk_open_usd","risk_open_pct","trade_id","direction","volume","entry_price","exit_price","sl","tp","initial_risk_usd","pnl_usd","result_R","duration_min","details");
   FileSeek(file,0,SEEK_END);
   MqlDateTime dt; TimeToStruct(now,dt);
   FileWrite(file,
      TimeToString(now,TIME_DATE|TIME_SECONDS),
      TimeToString(PragueNow(),TIME_DATE|TIME_SECONDS),
      (long)AccountInfoInteger(ACCOUNT_LOGIN),event_name,source,symbol,LedgerAssetClass(symbol),CsvSafe(strategy),EnumToString(_Period),LedgerSession(now,symbol),
      StringFormat("%02d:%02d",dt.hour,dt.min),
      DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2),
      DoubleToString(g_snap.open_risk_usd,2),DoubleToString(g_detected_base_cap>0?g_snap.open_risk_usd/g_detected_base_cap*100.0:0.0,3),
      "","","","","","","","","","","",CsvSafe(details));
   FileClose(file);
  }

void UnifiedLedgerTrade(ulong pos_id,string symbol,string source,string strategy,string direction,double volume,double entry_price,double exit_price,double sl,double tp,double initial_risk_usd,double net_profit,double result_r,datetime time_in,datetime time_out)
  {
   if(!InpEnableUnifiedLedger) return;
   string file_name=StringFormat("Guardian_Ledger_%I64d.csv",AccountInfoInteger(ACCOUNT_LOGIN));
   int file=FileOpen(file_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,';');
   if(file==INVALID_HANDLE) return;
   if(FileSize(file)==0)
      FileWrite(file,"timestamp_server","timestamp_prague","account","event","source","symbol","asset_class","strategy","timeframe","session","hour_server","balance","equity","risk_open_usd","risk_open_pct","trade_id","direction","volume","entry_price","exit_price","sl","tp","initial_risk_usd","pnl_usd","result_R","duration_min","details");
   FileSeek(file,0,SEEK_END);
   MqlDateTime dt; TimeToStruct(time_out>0?time_out:TimeCurrent(),dt);
   double duration=(time_in>0 && time_out>=time_in)?(double)(time_out-time_in)/60.0:0.0;
   FileWrite(file,TimeToString(time_out>0?time_out:TimeCurrent(),TIME_DATE|TIME_SECONDS),TimeToString(PragueNow(),TIME_DATE|TIME_SECONDS),(long)AccountInfoInteger(ACCOUNT_LOGIN),"TRADE_CLOSED",source,symbol,LedgerAssetClass(symbol),CsvSafe(strategy),EnumToString(_Period),LedgerSession(time_out>0?time_out:TimeCurrent(),symbol),StringFormat("%02d:%02d",dt.hour,dt.min),DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2),DoubleToString(g_snap.open_risk_usd,2),DoubleToString(g_detected_base_cap>0?g_snap.open_risk_usd/g_detected_base_cap*100.0:0.0,3),(long)pos_id,direction,DoubleToString(volume,2),DoubleToString(entry_price,8),DoubleToString(exit_price,8),DoubleToString(sl,8),DoubleToString(tp,8),DoubleToString(initial_risk_usd,2),DoubleToString(net_profit,2),DoubleToString(result_r,3),DoubleToString(duration,1),"Cloture de position");
   FileClose(file);
  }

void BlackBoxLog(string event_name, string details)
  {
   if(!InpEnableBlackBox) return;

   string file_name = StringFormat("Guardian_BlackBox_%I64d.csv", AccountInfoInteger(ACCOUNT_LOGIN));
   int file = FileOpen(file_name, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
   if(file == INVALID_HANDLE)
     {
      PrintFormat("[BLACKBOX] Impossible d'ouvrir le journal. Erreur %d", GetLastError());
      return;
     }

   if(FileSize(file) == 0)
      FileWrite(file, "heure_prague", "evenement", "symbole", "balance", "equity", "details");

   FileSeek(file, 0, SEEK_END);
   FileWrite(file,
             TimeToString(PragueNow(), TIME_DATE|TIME_SECONDS),
             event_name,
             _Symbol,
             DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2),
             DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
             CsvSafe(details));
   FileClose(file);
  }

string StrategyStatsKey(string field, string symbol, string engine)
  {
   ulong id = GenerateAutoMagicNumber(symbol + "|" + engine);
   return StringFormat("FTMO_PRO_V970_STAT_%I64d_%I64u_%s", AccountInfoInteger(ACCOUNT_LOGIN), id, field);
  }

double ReadStat(string key)
  {
   return (GlobalVariableCheck(key) ? GlobalVariableGet(key) : 0.0);
  }

void AddStat(string key, double amount)
  {
   GlobalVariableSet(key, ReadStat(key) + amount);
  }

void RecordTradeOutcome(ulong pos_id,string symbol,string engine,string source,string direction,double volume,double entry_price,double exit_price,double sl,double tp,double initial_risk_usd,double net_profit,double result_r,datetime time_in,datetime time_out)
  {
   if(StringLen(engine)==0) engine=(source=="MANUAL"?"MANUAL":"UNTAGGED");
   string prefix=StrategyStatsKey("",symbol,engine);
   AddStat(prefix+"COUNT",1.0);
   if(net_profit>0.0) AddStat(prefix+"WIN",1.0);
   AddStat(prefix+"PNL",net_profit);
   AddStat(prefix+"RSUM",result_r);
   if(!InpEnableBlackBox) return;
   string file_name=StringFormat("Guardian_TradeStats_%I64d.csv",AccountInfoInteger(ACCOUNT_LOGIN));
   int file=FileOpen(file_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,';');
   if(file==INVALID_HANDLE) return;
   if(FileSize(file)==0) FileWrite(file,"trade_id","ouverture_serveur","fermeture_serveur","symbole","source","moteur","direction","volume","entree","sortie","sl","tp","risque_initial_usd","pnl","resultat_R","duree_min","session","heure_serveur","timeframe","asset_class");
   FileSeek(file,0,SEEK_END);
   double duration=(time_in>0 && time_out>=time_in)?(double)(time_out-time_in)/60.0:0.0;
   MqlDateTime dt; TimeToStruct(time_in,dt);
   FileWrite(file,(long)pos_id,TimeToString(time_in,TIME_DATE|TIME_SECONDS),TimeToString(time_out,TIME_DATE|TIME_SECONDS),symbol,source,CsvSafe(engine),direction,DoubleToString(volume,2),DoubleToString(entry_price,8),DoubleToString(exit_price,8),DoubleToString(sl,8),DoubleToString(tp,8),DoubleToString(initial_risk_usd,2),DoubleToString(net_profit,2),DoubleToString(result_r,2),DoubleToString(duration,1),LedgerSession(time_in,symbol),StringFormat("%02d:%02d",dt.hour,dt.min),EnumToString(_Period),LedgerAssetClass(symbol));
   FileClose(file);
  }


string EventBadge(string event_name)
  {
   if(StringFind(event_name, "MANUAL_") == 0)
     {
      if(StringFind(event_name, "RISK") >= 0 || StringFind(event_name, "UNSAFE") >= 0) return "[$][!]";
      if(StringFind(event_name, "SL") >= 0) return "[LOCK][SL]";
      if(StringFind(event_name, "BE") >= 0) return "[LOCK][BE]";
      if(StringFind(event_name, "TP") >= 0) return "[$][TP]";
      if(StringFind(event_name, "POSITION") >= 0) return "[CUT][$]";
      if(StringFind(event_name, "ENTRY") >= 0 || StringFind(event_name, "ADOPTED") >= 0) return "[MANUAL][OK]";
      return "[MANUAL]";
     }
   if(StringFind(event_name, "EMERGENCY") >= 0 || StringFind(event_name, "STOP") >= 0) return "[LOCK][!]";
   if(StringFind(event_name, "TRADE") >= 0) return "[TRADE]";
   return "[GUARDIAN]";
  }

string EventHeadline(string event_name)
  {
   if(event_name == "MANUAL_ENTRY_DETECTED") return "NOUVELLE ENTREE MANUELLE";
   if(event_name == "MANUAL_OWNER_ACQUIRED") return "GUARDIAN MANUEL OWNER";
   if(event_name == "MANUAL_OWNER_LOST") return "GUARDIAN MANUEL STANDBY";
   if(event_name == "MANUAL_SL_CONFIRMED") return "SL MANUEL CONFIRME";
   if(event_name == "MANUAL_ADOPTED") return "TRADE MANUEL PROTEGE";
   if(event_name == "MANUAL_SL_ADDED") return "SL AUTOMATIQUE AJOUTE";
   if(event_name == "MANUAL_TP_ADDED") return "TP AUTOMATIQUE AJOUTE";
   if(event_name == "MANUAL_BE") return "BREAK-EVEN + FRAIS";
   if(event_name == "MANUAL_TP50") return "TP 50% EXECUTE";
   if(event_name == "MANUAL_POSITION_REDUCED") return "POSITION REDUITE";
   if(event_name == "MANUAL_SL_TOO_CLOSE" || event_name == "MANUAL_SL_REJECTED") return "SL INEXPLOITABLE";
   if(event_name == "MANUAL_UNPROTECTED") return "TRADE NON PROTEGE";
   if(event_name == "MANUAL_RISK_UNSAFE") return "RISQUE MANUEL DANGEREUX";
   if(event_name == "MANUAL_RISK_REDUCTION_FAILED") return "REDUCTION DU RISQUE ECHOUEE";
   if(event_name == "MANUAL_BE_FAILED") return "BE NON EXECUTE";
   if(event_name == "MANUAL_TP50_FAILED") return "TP 50% NON EXECUTE";
   if(event_name == "MANUAL_TP50_SKIPPED") return "TP 50% NON APPLICABLE";
   if(event_name == "NEWS_PRE_CLOSE") return "FERMETURE PRE-NEWS";
   if(event_name == "NEWS_POSITION_CLOSED") return "POSITION FERMEE AVANT NEWS";
   if(event_name == "NEWS_PRE_CLOSE_FAILED") return "FERMETURE PRE-NEWS ECHOUEE";
   if(event_name == "NEWS_ENTRY_BLOCKED") return "ENTREE BLOQUEE NEWS PROP FIRM";
   return event_name;
  }

void NotifyEvent(string event_name, string details)
  {
   string clean_details = CsvSafe(details);
   string badge = EventBadge(event_name);
   string headline = EventHeadline(event_name);
   PrintFormat("%s %s | %s", badge, headline, clean_details);
   BlackBoxLog(event_name, clean_details);
   UnifiedLedgerEvent(event_name, clean_details);
  }

double NormalizeTradePrice(string symbol, double price)
  {
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   return NormalizeDouble(price, digits);
  }

bool IsStopLossValidForPosition(string symbol, ENUM_POSITION_TYPE type, double sl_price)
  {
   if(sl_price <= 0.0) return false;
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double min_distance = stops_level * point;
   double market_price = (type == POSITION_TYPE_BUY ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK));
   if(point <= 0.0 || market_price <= 0.0) return false;
   if(type == POSITION_TYPE_BUY) return (market_price - sl_price >= min_distance);
   return (sl_price - market_price >= min_distance);
  }

bool IsManualSLTechnicallySafe(string symbol, ENUM_POSITION_TYPE type, double sl_price)
  {
   if(!IsStopLossValidForPosition(symbol, type, sl_price)) return false;
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0.0) return false;
   int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freeze_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double spread = MathMax(0.0, ask - bid);
   double min_distance = MathMax(stops_level, freeze_level) * point;
   min_distance = MathMax(min_distance, spread * InpManualMinSpreadFactor);
   min_distance += InpManualMinExtraPoints * point;
   double market_price = (type == POSITION_TYPE_BUY ? bid : ask);
   if(type == POSITION_TYPE_BUY) return (market_price - sl_price >= min_distance);
   return (sl_price - market_price >= min_distance);
  }

int VolumeDigits(string symbol)
  {
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   int digits = 0;
   while(step < 1.0 && digits < 8) { step *= 10.0; digits++; }
   return digits;
  }

double NormalizeVolumeDown(string symbol, double volume)
  {
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double min_vol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0 || min_vol <= 0.0 || max_vol <= 0.0) return 0.0;
   volume = MathFloor(volume / step + 1e-9) * step;
   if(volume > max_vol) volume = max_vol;
   if(volume < min_vol) return 0.0;
   return NormalizeDouble(volume, VolumeDigits(symbol));
  }


//--- Paramètres manuels selon type d'actif (indépendant de DetectMarketClass — ordre de compile MQL5)
string ManualAssetClassTag(string symbol)
  {
   string u = symbol; StringToUpper(u);
   if(IsSymbolCrypto(u)) return "CRYPTO";
   if(StringFind(u,"XAU")>=0 || StringFind(u,"GOLD")>=0) return "GOLD";
   if(StringFind(u,"WTI")>=0 || StringFind(u,"USOIL")>=0 || StringFind(u,"XTI")>=0 ||
      StringFind(u,"UKOIL")>=0 || StringFind(u,"BRENT")>=0 || StringFind(u,"OIL")>=0) return "OIL";
   return "CLASSIC"; // Forex / index / other
  }

double ManualSL_ATR_MultFor(string symbol)
  {
   string cls = ManualAssetClassTag(symbol);
   if(cls == "CRYPTO") return InpManualSL_ATR_MultCrypto;
   if(cls == "GOLD")   return InpManualSL_ATR_MultGold;
   if(cls == "OIL")    return InpManualSL_ATR_MultOil;
   return InpManualSL_ATR_Mult;
  }

double ManualTP_R_For(string symbol)
  {
   string cls = ManualAssetClassTag(symbol);
   if(cls == "CRYPTO") return InpManualTP_R_Crypto;
   if(cls == "GOLD")   return InpManualTP_R_Gold;
   return InpManualTP_R;
  }

double ManualTP_ClosePctFor(string symbol)
  {
   if(ManualAssetClassTag(symbol) == "CRYPTO") return InpManualTP_ClosePctCrypto;
   return InpManualTP_ClosePercent;
  }

double ManualBE_Trigger_R_For(string symbol)
  {
   if(ManualAssetClassTag(symbol) == "CRYPTO") return InpManualBE_Trigger_R_Crypto;
   return InpManualBE_Trigger_R;
  }

double GetManualMaxRiskUSD()
  {
   if(g_detected_base_cap <= 0.0 || InpManualMaxRiskPct <= 0.0) return 0.0;
   return g_detected_base_cap * (InpManualMaxRiskPct / 100.0);
  }

//+------------------------------------------------------------------+
//| v11.15 - OWNER UNIQUE + GUARDIAN MANUEL ACCOUNT-WIDE             |
//+------------------------------------------------------------------+
string ManualOwnerCandidatePrefix()
  {
   return StringFormat("G115M_%I64d_",AccountInfoInteger(ACCOUNT_LOGIN));
  }

string SymbolOwnerCandidatePrefix()
  {
   return StringFormat("G115S_%I64d_%I64u_",AccountInfoInteger(ACCOUNT_LOGIN),GenerateAutoMagicNumber(_Symbol));
  }

long LowestAliveGuardianCandidate(string prefix,datetime now_local)
  {
   long winner=0;
   int total=GlobalVariablesTotal();
   for(int i=0;i<total;i++)
     {
      string name=GlobalVariableName(i);
      if(StringFind(name,prefix)!=0) continue;
      double hb=GlobalVariableGet(name);
      if(hb<=0.0 || (now_local-(datetime)hb)>MathMax(1,InpManualOwnerStaleSeconds)) continue;
      string suffix=StringSubstr(name,StringLen(prefix));
      long chart_id=(long)StringToInteger(suffix);
      if(chart_id<=0) continue;
      if(winner==0 || chart_id<winner) winner=chart_id;
     }
   return winner;
  }

void RefreshInstanceOwnership(bool force=false)
  {
   if(MQLInfoInteger(MQL_TESTER))
     {
      g_manual_guard_owner=true;
      g_symbol_instance_owner=true;
      return;
     }

   datetime now_local=TimeLocal();
   if(!force && g_last_owner_refresh_local>0 && now_local==g_last_owner_refresh_local) return;
   g_last_owner_refresh_local=now_local;

   if(g_instance_chart_id<=0) g_instance_chart_id=ChartID();
   if(g_manual_candidate_key=="")
      g_manual_candidate_key=ManualOwnerCandidatePrefix()+StringFormat("%I64d",g_instance_chart_id);
   if(g_symbol_candidate_key=="")
      g_symbol_candidate_key=SymbolOwnerCandidatePrefix()+StringFormat("%I64d",g_instance_chart_id);

   GlobalVariableSet(g_manual_candidate_key,(double)now_local);
   GlobalVariableSet(g_symbol_candidate_key,(double)now_local);

   bool old_manual=g_manual_guard_owner;
   bool old_symbol=g_symbol_instance_owner;

   long manual_winner=LowestAliveGuardianCandidate(ManualOwnerCandidatePrefix(),now_local);
   long symbol_winner=LowestAliveGuardianCandidate(SymbolOwnerCandidatePrefix(),now_local);

   g_manual_guard_owner=(!InpEnableAccountWideManualGuardian || manual_winner==0 || manual_winner==g_instance_chart_id);
   g_symbol_instance_owner=(symbol_winner==0 || symbol_winner==g_instance_chart_id);

   if(g_manual_guard_owner!=old_manual)
     {
      if(g_manual_guard_owner)
         NotifyEvent("MANUAL_OWNER_ACQUIRED",StringFormat("chart=%I64d | symbole=%s | protection Magic0 account-wide ACTIVE",g_instance_chart_id,_Symbol));
      else
         NotifyEvent("MANUAL_OWNER_LOST",StringFormat("chart=%I64d | symbole=%s | gestion manuelle reprise par une autre instance",g_instance_chart_id,_Symbol));
     }

   if(g_symbol_instance_owner!=old_symbol && InpEnableExitDebugLogs)
      PrintFormat("[INSTANCE_OWNER] %s | chart=%I64d | %s",_Symbol,g_instance_chart_id,(g_symbol_instance_owner?"OWNER":"STANDBY"));
  }

void ReleaseInstanceOwnership()
  {
   if(g_manual_candidate_key!="" && GlobalVariableCheck(g_manual_candidate_key))
      GlobalVariableDel(g_manual_candidate_key);
   if(g_symbol_candidate_key!="" && GlobalVariableCheck(g_symbol_candidate_key))
      GlobalVariableDel(g_symbol_candidate_key);
  }

bool FindPositionTicketByIdentifier(ulong pos_id,string symbol,ulong &ticket)
  {
   ticket=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong t=PositionGetTicket(i);
      if(t==0) continue;
      if(symbol!="" && PositionGetString(POSITION_SYMBOL)!=symbol) continue;
      ulong identifier=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(identifier==pos_id || t==pos_id)
        {
         ticket=t;
         return true;
        }
     }
   return false;
  }

ENUM_TIMEFRAMES ManualSetupTFFor(string symbol)
  {
   return (IsSymbolCrypto(symbol) ? InpCryptoSetupTF : InpClassicSetupTF);
  }

bool GetManualATRValue(string symbol,double &atr_value)
  {
   atr_value=0.0;
   if(symbol=="") return false;
   SymbolSelect(symbol,true);
   int h=iATR(symbol,ManualSetupTFFor(symbol),InpATR_Period);
   if(h==INVALID_HANDLE) return false;
   double a[1];
   bool ok=(CopyBuffer(h,0,1,1,a)>=1 && a[0]>0.0);
   if(ok) atr_value=a[0];
   IndicatorRelease(h);
   return ok;
  }

double ManualTPPrice(ulong ticket, double open_price, ENUM_POSITION_TYPE type, double initial_risk_dist, double volume)
  {
   string symbol=_Symbol;
   if(PositionSelectByTicket(ticket))
      symbol=PositionGetString(POSITION_SYMBOL);
   double cost_points = 0.0;
   double be_price = GetTrueBreakEvenPrice(ticket, open_price, type, volume, cost_points);
   double cost_dist = MathAbs(be_price - open_price);
   double tp_r = ManualTP_R_For(symbol);
   double target_dist = (initial_risk_dist * tp_r) + cost_dist;
   double target = (type == POSITION_TYPE_BUY ? open_price + target_dist : open_price - target_dist);
   return NormalizeTradePrice(symbol, target);
  }

bool ReduceManualRiskToCap(ulong ticket, double max_risk_usd)
  {
   // v11.15 : toujours utiliser le symbole REEL de la position.
   if(max_risk_usd <= 0.0 || !PositionSelectByTicket(ticket)) return false;

   string symbol=PositionGetString(POSITION_SYMBOL);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double min_vol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   if(step <= 0.0 || min_vol <= 0.0) return false;

   double current_risk = CalculateOpenPositionRisk(ticket);
   if(current_risk <= max_risk_usd + 0.01) return true;

   double current_volume = PositionGetDouble(POSITION_VOLUME);
   if(current_volume <= 0.0) return false;

   double target_volume = current_volume * (max_risk_usd / current_risk) * 0.995;
   target_volume = NormalizeVolumeDown(symbol, target_volume);

   double close_volume = current_volume - target_volume;
   close_volume = MathFloor(close_volume / step + 1e-9) * step;
   close_volume = NormalizeDouble(close_volume, VolumeDigits(symbol));

   if(target_volume < min_vol)
     {
      NotifyEvent("MANUAL_RISK_UNSAFE", StringFormat("#%I64u | %s | risque %.2f$ > %.2f$ | lot minimum incompatible : fermeture complete", ticket, symbol, current_risk, max_risk_usd));
      return g_trade.PositionClose(ticket);
     }

   if(close_volume < min_vol || (current_volume - close_volume) < min_vol)
     {
      NotifyEvent("MANUAL_RISK_UNSAFE", StringFormat("#%I64u | %s | risque %.2f$ > %.2f$ | reduction impossible proprement", ticket, symbol, current_risk, max_risk_usd));
      return false;
     }

   double volume_before = current_volume;

   if(!g_trade.PositionClosePartial(ticket, close_volume))
     {
      NotifyEvent("MANUAL_RISK_REDUCTION_FAILED", StringFormat("#%I64u | %s | %s", ticket, symbol, g_trade.ResultRetcodeDescription()));
      return false;
     }

   if(!PositionSelectByTicket(ticket))
     {
      NotifyEvent("MANUAL_RISK_REDUCTION_PENDING", StringFormat("#%I64u | %s | fermeture partielle acceptee mais position temporairement non rafraichie", ticket, symbol));
      return true;
     }

   double volume_after = PositionGetDouble(POSITION_VOLUME);
   double new_risk = CalculateOpenPositionRisk(ticket);

   if(volume_after < volume_before - (step * 0.5))
     {
      NotifyEvent("MANUAL_POSITION_REDUCED", StringFormat("#%I64u | %s | %.2f$ -> %.2f$ risque | volume %.2f -> %.2f | ferme %.2f lot", ticket, symbol, current_risk, new_risk, volume_before, volume_after, close_volume));
      if(new_risk <= max_risk_usd + 1.0) return true;
      NotifyEvent("MANUAL_RISK_REDUCTION_PENDING", StringFormat("#%I64u | %s | reduction appliquee | risque encore %.2f$ > %.2f$ | reevaluation", ticket, symbol, new_risk, max_risk_usd));
      return true;
     }

   NotifyEvent("MANUAL_RISK_REDUCTION_PENDING", StringFormat("#%I64u | %s | reduction acceptee mais volume non rafraichi (%.2f lot)", ticket, symbol, volume_after));
   return true;
  }

//+------------------------------------------------------------------+
//| TRUE BREAK-EVEN AVEC FRAIS ET SPREAD                             |
//+------------------------------------------------------------------+
double EstimatedCommissionPerSideUSD(string symbol,double volume,double price)
  {
   if(!InpUseFallbackCommissionModel || volume<=0.0) return 0.0;
   if(price<=0.0)
      price=(SymbolInfoDouble(symbol,SYMBOL_ASK)+SymbolInfoDouble(symbol,SYMBOL_BID))*0.5;
   if(IsSymbolCrypto(symbol))
     {
      double contract=SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE);
      if(contract<=0.0 || price<=0.0) return 0.0;
      return price*contract*volume*(InpCryptoCommissionPctPerSide/100.0);
     }
   if(LedgerAssetClass(symbol)=="FOREX")
      return MathMax(0.0,InpForexCommissionPerLotPerSide)*volume;
   return 0.0;
  }

double GetTrueBreakEvenPrice(ulong ticket, double open_price, ENUM_POSITION_TYPE type, double volume, double &out_cost_points)
  {
   string symbol=_Symbol;
   ulong position_id=ticket;
   double swap_value=0.0;
   if(PositionSelectByTicket(ticket))
     {
      symbol=PositionGetString(POSITION_SYMBOL);
      ulong identifier=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(identifier>0) position_id=identifier;
      swap_value=PositionGetDouble(POSITION_SWAP);
     }

   double total_commissions_usd = 0.0;
   if(HistorySelectByPosition(position_id))
     {
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         ulong d_ticket = HistoryDealGetTicket(i);
         if(HistoryDealGetInteger(d_ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
            total_commissions_usd += MathAbs(HistoryDealGetDouble(d_ticket, DEAL_COMMISSION));
        }
     }

   double fallback_round_trip=2.0*EstimatedCommissionPerSideUSD(symbol,volume,open_price);
   double estimated_total_cost = (total_commissions_usd>0.0 ? total_commissions_usd*2.0 : fallback_round_trip)
                                 + MathAbs(swap_value);
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);

   out_cost_points = 0.0;
   if(tick_value > 0 && volume > 0 && tick_size > 0)
      out_cost_points = (estimated_total_cost / (tick_value * volume)) * tick_size;

   double true_be_price = 0.0;
   if(type == POSITION_TYPE_BUY)
      true_be_price = open_price + out_cost_points + (InpBE_BufferPoints * point);
   else
      true_be_price = open_price - out_cost_points - (InpBE_BufferPoints * point);

   return NormalizeTradePrice(symbol,true_be_price);
  }

//+------------------------------------------------------------------+
//| CLÔTURE & BLACKBOX                                               |
//+------------------------------------------------------------------+
void QueueClosedPosition(ulong pos_id)
  {
   if(pos_id == 0) return;
   for(int i=0; i<g_pending_closed_count; i++)
      if(g_pending_closed_positions[i] == pos_id) return;
   if(g_pending_closed_count < ArraySize(g_pending_closed_positions))
      g_pending_closed_positions[g_pending_closed_count++] = pos_id;
  }

bool PositionIdentifierStillOpen(ulong pos_id)
  {
   if(pos_id == 0) return false;
   for(int i=PositionsTotal()-1; i>=0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if((ulong)PositionGetInteger(POSITION_IDENTIFIER) == pos_id)
         return true;
     }
   return false;
  }

void ProcessPendingClosedPositions()
  {
   for(int i=g_pending_closed_count-1; i>=0; i--)
     {
      ulong pos_id = g_pending_closed_positions[i];
      if(PositionIdentifierStillOpen(pos_id))
         continue;

      ProcessClosedPosition(pos_id);

      for(int j=i; j<g_pending_closed_count-1; j++)
         g_pending_closed_positions[j] = g_pending_closed_positions[j+1];
      g_pending_closed_count--;
     }
  }

void ProcessClosedPosition(ulong pos_id)
  {
   if(!HistorySelectByPosition(pos_id)) return;
   int deals_total=HistoryDealsTotal();
   double total_profit=0.0,total_commission=0.0,total_swap=0.0,total_fee=0.0;
   string symbol="",comment="",direction="";
   long first_magic=-1;
   double volume=0.0,entry_price=0.0,exit_price=0.0,sl=0.0,tp=0.0;
   datetime time_in=0,time_out=0;
   for(int i=0;i<deals_total;i++)
     {
      ulong d_ticket=HistoryDealGetTicket(i);
      long entry=HistoryDealGetInteger(d_ticket,DEAL_ENTRY);
      if(entry==DEAL_ENTRY_IN)
        {
         if(time_in==0) time_in=(datetime)HistoryDealGetInteger(d_ticket,DEAL_TIME);
         if(symbol=="") symbol=HistoryDealGetString(d_ticket,DEAL_SYMBOL);
         if(first_magic<0) first_magic=HistoryDealGetInteger(d_ticket,DEAL_MAGIC);
         if(comment=="") comment=HistoryDealGetString(d_ticket,DEAL_COMMENT);
         if(volume<=0.0) volume=HistoryDealGetDouble(d_ticket,DEAL_VOLUME);
         if(entry_price<=0.0) entry_price=HistoryDealGetDouble(d_ticket,DEAL_PRICE);
         long dtype=HistoryDealGetInteger(d_ticket,DEAL_TYPE);
         direction=(dtype==DEAL_TYPE_BUY?"BUY":"SELL");
        }
      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_INOUT)
        {
         time_out=(datetime)HistoryDealGetInteger(d_ticket,DEAL_TIME);
         exit_price=HistoryDealGetDouble(d_ticket,DEAL_PRICE);
         total_profit+=HistoryDealGetDouble(d_ticket,DEAL_PROFIT);
         total_commission+=HistoryDealGetDouble(d_ticket,DEAL_COMMISSION);
         total_swap+=HistoryDealGetDouble(d_ticket,DEAL_SWAP);
         total_fee+=HistoryDealGetDouble(d_ticket,DEAL_FEE);
        }
     }
   if(symbol=="") symbol=_Symbol;
   bool is_manual=(first_magic==0);
   string source=(is_manual?"MANUAL":"AUTO");
   string engine=(is_manual?"MANUAL":comment);
   if(StringLen(engine)==0) engine=(is_manual?"MANUAL":"AUTO_UNTAGGED");
   double net_profit=total_profit+total_commission+total_swap+total_fee;
   double initial_risk_usd=0.0; GetTicketState("RISKUSD",pos_id,initial_risk_usd);
   if(initial_risk_usd<=0.0) initial_risk_usd=CalculateOpenPositionRisk(pos_id);
   double result_r=(initial_risk_usd>0.0?net_profit/initial_risk_usd:0.0);
   if(PositionSelectByTicket(pos_id)) { sl=PositionGetDouble(POSITION_SL); tp=PositionGetDouble(POSITION_TP); }
   // If the position is already gone, recover last known SL/TP from state when available is not possible; keep 0 rather than invent.
   RecordTradeOutcome(pos_id,symbol,engine,source,direction,volume,entry_price,exit_price,sl,tp,initial_risk_usd,net_profit,result_r,time_in,time_out);
   UnifiedLedgerTrade(pos_id,symbol,source,engine,direction,volume,entry_price,exit_price,sl,tp,initial_risk_usd,net_profit,result_r,time_in,time_out);
   if(net_profit<0.0)
     {
      g_consecutive_losses=GetAccountConsecutiveLosses()+1;
      datetime cd = 0;
      if(InpConsecutiveLossCooldown > 0 && g_consecutive_losses >= InpConsecutiveLossCooldown)
        {
         int mins = InpCooldownMinutesAfterLosses;
         bool crypto_loss = IsSymbolCrypto(symbol);
         if(crypto_loss && InpCryptoCooldownMinutes > mins)
            mins = InpCryptoCooldownMinutes;
         double mult = 1.0;
         if(g_consecutive_losses >= InpConsecutiveLossCooldown * 3) mult = 2.0;
         else if(g_consecutive_losses >= InpConsecutiveLossCooldown * 2) mult = 1.5;
         cd = TimeCurrent() + (datetime)(mins * 60 * mult);
         if(crypto_loss)
           {
            // Cooldown crypto-only : le Forex continue de trader
            g_crypto_cooldown_to = cd;
            GlobalVariableSet(AccountStateGV("CRYPTO_CD"), (double)cd);
            SetAccountCooldownState(g_consecutive_losses, 0); // pas de freeze compte entier
           }
         else
           {
            SetAccountCooldownState(g_consecutive_losses, cd);
           }
        }
      else
         SetAccountCooldownState(g_consecutive_losses, 0);
      NotifyEvent("TRADE_CLOSED_LOSS",StringFormat("#%I64u | %s | %s | PnL %.2f | pertes consecutives %d | cooldown %d min | scope %s",pos_id,source,engine,net_profit,g_consecutive_losses,(cd>TimeCurrent()?(int)((cd-TimeCurrent())/60):0),(IsSymbolCrypto(symbol)?"CRYPTO":"ACCOUNT")));
     }
   else if(net_profit>0.0)
     {
      NotifyEvent("TRADE_CLOSED_WIN",StringFormat("#%I64u | %s | %s | PnL +%.2f",pos_id,source,engine,net_profit));
      SetAccountCooldownState(0,0);
      g_crypto_cooldown_to = 0;
      GlobalVariableSet(AccountStateGV("CRYPTO_CD"), 0);
     }
   DeleteTicketState(pos_id);
  }


void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0) return;
   if(!HistoryDealSelect(trans.deal)) return;

   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
   string deal_symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   ulong pos_id = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);

   // Toute entrée Magic 0 devient explicitement une position manuelle Guardian.
   if(InpAdoptManualTrades && magic==0 && (entry==DEAL_ENTRY_IN || entry==DEAL_ENTRY_INOUT))
     {
      SetTicketState("MANUAL",pos_id,1.0);
      double old_detect=0.0;
      if(!GetTicketState("DETECTMS",pos_id,old_detect) || old_detect<=0.0)
         SetTicketState("DETECTMS",pos_id,(double)GetTickCount64());
     }

   double manual_state=0.0;
   bool is_known_manual=(GetTicketState("MANUAL",pos_id,manual_state) && manual_state>0.5);
   bool is_manual=(InpAdoptManualTrades && (magic==0 || is_known_manual));
   bool is_our_auto=(deal_symbol==_Symbol && magic==(long)g_auto_magic);

   if(!is_manual && !is_our_auto) return;

   // Rafraîchissement forcé lors d'un trade : évite deux owners transitoires.
   RefreshInstanceOwnership(true);
   if(is_manual && !g_manual_guard_owner) return;
   if(is_our_auto && !g_symbol_instance_owner) return;
   if(is_manual && !g_prop_execution_authorized)
     {
      if(entry==DEAL_ENTRY_IN || entry==DEAL_ENTRY_INOUT)
         PrintFormat("[MANUAL_GUARD] %s #%I64u detecte mais aucune requete EA: %s",deal_symbol,pos_id,g_prop_execution_reason);
      return;
     }

   if(entry == DEAL_ENTRY_IN || entry == DEAL_ENTRY_INOUT)
     {
      double vol = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
      double price = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
      string comment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
      if(is_manual)
        {
         NotifyEvent("MANUAL_ENTRY_DETECTED", StringFormat("#%I64u | %s | %s | %.2f lot @ %.5f | ordre manuel/mobile | commentaire: %s",
                     pos_id,deal_symbol,(HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_BUY ? "BUY" : "SELL"),vol,price,comment));

         // Tentative immédiate. Si la position/ATR n'est pas encore visible,
         // OnTimer réessaie sous InpManualGuardianTimerMs.
         ulong ticket=0;
         if(FindPositionTicketByIdentifier(pos_id,deal_symbol,ticket))
            ManageManualPosition(ticket,true);
        }
      else
        {
         NotifyEvent("AUTO_ENTRY_FILLED", StringFormat("#%I64u | %s | %.2f lot @ %.5f | commentaire: %s",
                     pos_id,(HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_BUY ? "BUY" : "SELL"),vol,price,comment));
        }
     }

   if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT)
     {
      bool still_open = PositionIdentifierStillOpen(pos_id);
      if(!still_open) QueueClosedPosition(pos_id);
     }
  }

//+------------------------------------------------------------------+
//| RISK, LOTS ET HEARTBEAT GUARDIAN                                 |
//+------------------------------------------------------------------+
int CountAccountPositions() { return PositionsTotal(); }

int CountSymbolPositions(string symbol) 
  { 
   int count = 0; 
   for(int i = PositionsTotal() - 1; i >= 0; i--) 
     { 
      ulong ticket = PositionGetTicket(i); 
      if(ticket == 0) continue; 
      if(PositionGetString(POSITION_SYMBOL) == symbol) count++; 
     } 
   return count; 
  }
  
double CalculateOpenPositionRisk(ulong ticket) 
  { 
   if(!PositionSelectByTicket(ticket)) return 0.0; 
   string symbol = PositionGetString(POSITION_SYMBOL); 
   ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE); 
   double open_price = PositionGetDouble(POSITION_PRICE_OPEN); 
   double sl_price = PositionGetDouble(POSITION_SL); 
   double volume = PositionGetDouble(POSITION_VOLUME); 
   
   if(sl_price <= 0.0) return g_detected_base_cap * 0.015;
   if(type == POSITION_TYPE_BUY && sl_price >= open_price) return 0.0; 
   if(type == POSITION_TYPE_SELL && sl_price <= open_price) return 0.0; 
   
   double profit = 0.0; 
   ENUM_ORDER_TYPE ot = (type == POSITION_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL); 
   double round_trip_cost=2.0*EstimatedCommissionPerSideUSD(symbol,volume,open_price);
   if(OrderCalcProfit(ot, symbol, volume, open_price, sl_price, profit)) return MathAbs(profit)+round_trip_cost; 
   
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE); 
   double tick_val = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS); 
   if(tick_val <= 0.0) tick_val = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE); 
   if(tick_size > 0.0 && tick_val > 0.0) return (MathAbs(open_price - sl_price) / tick_size) * tick_val * volume+round_trip_cost; 
   
   return 0.0; 
  }
  
double CalculateTotalOpenAccountRisk() 
  { 
   double total = 0.0; 
   for(int i = PositionsTotal() - 1; i >= 0; i--) 
     { 
      ulong ticket = PositionGetTicket(i); 
      if(ticket == 0) continue; 
      total += CalculateOpenPositionRisk(ticket); 
     } 
   return total; 
  }
  
double GetEffectiveRiskPercent() 
  { 
   // Le risque est adapte a la perte du jour, avant le coupe-circuit Guardian.
   double daily_loss_pct = (g_detected_base_cap > 0.0 ? (g_snap.daily_loss / g_detected_base_cap) * 100.0 : 0.0);
   double dd_step1=(IsTightOneStepProfile() ? MathMin(InpDDRiskStep1Pct,1.00) : InpDDRiskStep1Pct);
   double dd_step2=(IsTightOneStepProfile() ? MathMin(InpDDRiskStep2Pct,2.00) : InpDDRiskStep2Pct);
   double stop_new=(IsTightOneStepProfile() ? MathMin(InpStopNewTradesDailyPct,2.50) : InpStopNewTradesDailyPct);
   if(InpEnableDailyRiskScaling)
     {
      if(daily_loss_pct >= stop_new) return 0.0;
      if(daily_loss_pct >= dd_step2) return MathMin(InpRiskPerTradePct, InpDDRiskStep2TradePct);
      if(daily_loss_pct >= dd_step1) return MathMin(InpRiskPerTradePct, InpDDRiskStep1TradePct);
     }

   double total_profit_pct = ((AccountInfoDouble(ACCOUNT_EQUITY) - g_detected_base_cap) / g_detected_base_cap) * 100.0;
   double risk_pct = InpRiskPerTradePct;
   if(InpEnableSmoothLanding && total_profit_pct >= InpSmoothLandingTriggerPct)
      risk_pct = MathMin(risk_pct, InpSmoothLandingRiskPct);
   return risk_pct; 
  }

double CalculateLossAtLot(string symbol,double sl_distance_points,ENUM_ORDER_TYPE order_type,double lots,double &loss_usd)
  {
   loss_usd=0.0;
   if(sl_distance_points<=0.0 || lots<=0.0) return false;
   double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
   double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
   double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
   if(point<=0.0 || ask<=0.0 || bid<=0.0) return false;
   double entry=(order_type==ORDER_TYPE_BUY ? ask : bid);
   double sl=(order_type==ORDER_TYPE_BUY ? entry-sl_distance_points*point : entry+sl_distance_points*point);
   double raw=0.0;
   double round_trip_cost=2.0*EstimatedCommissionPerSideUSD(symbol,lots,entry);
   if(OrderCalcProfit(order_type,symbol,lots,entry,sl,raw))
     { loss_usd=MathAbs(raw)+round_trip_cost; return loss_usd>0.0; }
   double tick_sz=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tick_vl=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_vl<=0.0) tick_vl=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tick_sz<=0.0 || tick_vl<=0.0) return false;
   loss_usd=MathAbs((sl_distance_points*point)/tick_sz*tick_vl*lots)+round_trip_cost;
   return loss_usd>0.0;
  }

double CalculateDynamicLot(string symbol, double sl_distance_points, double risk_percent, ENUM_ORDER_TYPE order_type) 
  { 
   if(sl_distance_points <= 0.0) return 0.0; 
   double risk_usd = g_detected_base_cap * (risk_percent / 100.0); 
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT); 
   double min_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN); 
   double max_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX); 
   double step_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP); 
   
   if(point <= 0.0 || min_lot <= 0.0 || max_lot <= 0.0 || step_lot <= 0.0) return 0.0; 
   
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK); 
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID); 
   double entry_price = (order_type == ORDER_TYPE_BUY ? ask : bid);
   double sl_price = (order_type == ORDER_TYPE_BUY ? entry_price - (sl_distance_points * point) : entry_price + (sl_distance_points * point)); 
   double one_lot_loss = 0.0; 
   
   if(!OrderCalcProfit(order_type, symbol, 1.0, entry_price, sl_price, one_lot_loss) || one_lot_loss >= 0.0) 
     { 
      double tick_sz = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE); 
      double tick_vl = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS); 
      if(tick_vl <= 0.0) tick_vl = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE); 
      if(tick_sz <= 0.0 || tick_vl <= 0.0) return 0.0; 
      one_lot_loss = -((sl_distance_points * point) / tick_sz) * tick_vl; 
     } 
     
   double loss_per_lot = MathAbs(one_lot_loss)+2.0*EstimatedCommissionPerSideUSD(symbol,1.0,entry_price); 
   if(loss_per_lot <= 0.0) return 0.0; 
   
   double raw_lots = (risk_usd / loss_per_lot); 
   double lots = MathFloor(raw_lots / step_lot + 1e-9) * step_lot; 
   double margin_required = 0.0; 
   
   if(OrderCalcMargin(order_type, symbol, lots, entry_price, margin_required)) 
     { 
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE); 
      if(margin_required > free_margin && margin_required > 0.0) 
         lots = MathFloor((lots * (free_margin / margin_required)) / step_lot + 1e-9) * step_lot; 
     } 
     
   if(lots < min_lot) return 0.0; 
   if(lots > max_lot) lots = max_lot; 
   
   int decimals = 0; 
   double temp = step_lot; 
   while(temp < 1.0 && decimals < 8) { temp *= 10.0; decimals++; } 
   return NormalizeDouble(lots, decimals); 
  }

bool IsRelativeTickVolumePresent()
  {
   // Crypto CFD: tick volume souvent irrégulier — désactivé par défaut (V1).
   if(g_market_class == MARKET_CRYPTO)
     {
      if(!InpRequireVolumeSpikeCrypto) return true;
     }
   else if(!InpRequireVolumeSpike) return true;

   long vol_history[];
   ArraySetAsSeries(vol_history, true);
   ENUM_TIMEFRAMES vtf = (g_market_class == MARKET_CRYPTO ? InpCryptoSetupTF : _Period);
   if(CopyTickVolume(_Symbol, vtf, 1, 20, vol_history) < 20) return false;

   long sum = 0;
   for(int i = 1; i < 20; i++) sum += vol_history[i];
   double avg_vol = (double)sum / 19.0;
   return (vol_history[0] >= (avg_vol * 1.25));
  }

datetime PragueMidnightAsUTC(datetime prague_day_key)
  {
   // PragueDayKey porte la date Prague sous forme de datetime "neutre".
   // A minuit, le decalage est determine sur l'heure UTC correspondante.
   datetime utc_guess = prague_day_key - 3600;
   int prague_offset = IsPragueSummerTime(utc_guess) ? 2 : 1;
   return prague_day_key - (prague_offset * 3600);
  }

double BalanceRecordedAtPragueMidnight(datetime prague_day_key, double fallback_balance)
  {
   // Reconstitue la balance de minuit en retirant les operations de la journee.
   // Cela couvre un redemarrage de l'EA apres minuit tant que l'historique est disponible.
   if(MQLInfoInteger(MQL_TESTER)) return fallback_balance;

   datetime server_now = TimeTradeServer();
   datetime utc_now = TimeGMT();
   if(server_now <= 0 || utc_now <= 0) return fallback_balance;
   long server_utc_offset = (long)(server_now - utc_now);
   datetime day_start_server = (datetime)(PragueMidnightAsUTC(prague_day_key) + server_utc_offset);
   if(!HistorySelect(day_start_server, server_now)) return fallback_balance;

   double reconstructed_balance = fallback_balance;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong deal_ticket = HistoryDealGetTicket(i);
      datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
      if(deal_time < day_start_server) continue;
      reconstructed_balance -= HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
      reconstructed_balance -= HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
      reconstructed_balance -= HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
      reconstructed_balance -= HistoryDealGetDouble(deal_ticket, DEAL_FEE);
     }

   return (reconstructed_balance > 0.0 ? reconstructed_balance : fallback_balance);
  }

double BalanceRecordedAtComplianceMidnight(datetime day_key,double fallback_balance)
  {
   if(!ComplianceUsesServerMidnight()) return BalanceRecordedAtPragueMidnight(day_key,fallback_balance);
   if(MQLInfoInteger(MQL_TESTER)) return fallback_balance;
   datetime server_now=TimeTradeServer(); if(server_now<=0) return fallback_balance;
   if(!HistorySelect(day_key,server_now)) return fallback_balance;
   double reconstructed=fallback_balance;
   for(int i=0;i<HistoryDealsTotal();i++)
     {
      ulong d=HistoryDealGetTicket(i);
      datetime t=(datetime)HistoryDealGetInteger(d,DEAL_TIME);
      if(t<day_key) continue;
      reconstructed-=HistoryDealGetDouble(d,DEAL_PROFIT);
      reconstructed-=HistoryDealGetDouble(d,DEAL_COMMISSION);
      reconstructed-=HistoryDealGetDouble(d,DEAL_SWAP);
      reconstructed-=HistoryDealGetDouble(d,DEAL_FEE);
     }
   return (reconstructed>0.0 ? reconstructed : fallback_balance);
  }

void GuardianHeartbeat() 
  { 
   datetime day_key = ComplianceDayKeyNow(); 
   // Nouveaux noms : une ancienne reference equity de v9.56 ne doit jamais etre reutilisee.
   string gv_day_name = StringFormat("PFG_GUARD_V112_DAYKEY_%I64d", AccountInfoInteger(ACCOUNT_LOGIN));
   string gv_start_name = StringFormat("PFG_GUARD_V112_START_BAL_%I64d", AccountInfoInteger(ACCOUNT_LOGIN));
   string gv_peak_name = StringFormat("PFG_GUARD_V112_PEAK_EOD_%I64d", AccountInfoInteger(ACCOUNT_LOGIN));
   
   double current_bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double current_eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   
   double stored_ref = 0.0; 
   if(!GlobalVariableCheck(gv_day_name) || (datetime)GlobalVariableGet(gv_day_name) != day_key) 
     { 
      GlobalVariableSet(gv_day_name, (double)day_key); 
      // FTMO utilise la BALANCE de minuit CE(S)T, jamais l'equity flottante.
      // La reconstruction couvre notamment un redemarrage apres minuit.
       stored_ref = BalanceRecordedAtComplianceMidnight(day_key, current_bal);
       GlobalVariableSet(gv_start_name, stored_ref); 
       double peak_ref=(GlobalVariableCheck(gv_peak_name) ? GlobalVariableGet(gv_peak_name) : g_detected_base_cap);
       peak_ref=MathMax(g_detected_base_cap,MathMax(peak_ref,stored_ref));
       GlobalVariableSet(gv_peak_name,peak_ref);
     } 
   else 
     { 
      stored_ref = GlobalVariableGet(gv_start_name); 
      if(stored_ref <= 0.0)
        {
         stored_ref = current_bal;
         GlobalVariableSet(gv_start_name, stored_ref);
        }
     } 
     
   g_snap.balance = current_bal; 
   g_snap.equity = current_eq; 
   g_snap.daily_start_reference = stored_ref; 
   
   double daily_pnl = g_snap.equity - g_snap.daily_start_reference; 
   g_snap.daily_profit = daily_pnl; 
   g_snap.daily_loss = (daily_pnl < 0.0 ? MathAbs(daily_pnl) : 0.0); 
   
   double ftmo_daily_pct=ComplianceDailyLossPct();
   double guardian_daily_pct=MathMin(InpGuardianDailyStopPct,MathMax(0.10,ftmo_daily_pct-0.20));
   double daily_stop_usd = g_detected_base_cap * (guardian_daily_pct / 100.0); 
   double daily_floor_guardian = g_snap.daily_start_reference - daily_stop_usd; 
   double daily_floor_ftmo = g_snap.daily_start_reference - (g_detected_base_cap * (ftmo_daily_pct / 100.0));
   
   g_snap.daily_remaining_guardian = g_snap.equity - daily_floor_guardian; 
   
   double peak_eod=(GlobalVariableCheck(gv_peak_name) ? GlobalVariableGet(gv_peak_name) : g_detected_base_cap);
   peak_eod=MathMax(g_detected_base_cap,peak_eod);
   double overall_anchor=(ComplianceOverallTrailingEOD() ? peak_eod : g_detected_base_cap);
   double max_overall_stop_usd = g_detected_base_cap * (InpGuardianOverallStopPct / 100.0); 
   double overall_floor_guardian = overall_anchor - max_overall_stop_usd; 
   double overall_floor_ftmo = overall_anchor - (g_detected_base_cap * (ComplianceOverallLossPct() / 100.0));
   g_snap.overall_remaining_guardian = g_snap.equity - overall_floor_guardian; 
   
   g_snap.open_risk_usd = CalculateTotalOpenAccountRisk(); 
   g_snap.total_account_positions = CountAccountPositions(); 
   g_snap.symbol_positions = CountSymbolPositions(_Symbol); 
   
   // MathMax choisit toujours le plancher le plus protecteur. Les limites FTMO
   // restent ainsi un filet de securite si un parametre Guardian est mal saisi.
   double effective_overall_floor = MathMax(overall_floor_guardian, overall_floor_ftmo);
   double effective_daily_floor = MathMax(daily_floor_guardian, daily_floor_ftmo);
   if(g_snap.equity <= effective_overall_floor) g_snap.state = GUARDIAN_LOCKED; 
   else if(g_snap.equity <= effective_daily_floor) g_snap.state = GUARDIAN_FORCE_CLOSE; 
   else if(g_snap.daily_remaining_guardian <= (g_detected_base_cap * 0.0075)) g_snap.state = GUARDIAN_WARNING; 
   else g_snap.state = GUARDIAN_NORMAL; 

   bool emergency = (g_snap.state == GUARDIAN_FORCE_CLOSE || g_snap.state == GUARDIAN_LOCKED);
   if(emergency && !g_emergency_event_announced)
     {
      g_emergency_event_announced = true;
      NotifyEvent("EMERGENCY_CLOSE", StringFormat("Equity %.2f | seuil journalier Guardian %.2f%% | reference balance %.2f", g_snap.equity, guardian_daily_pct, stored_ref));
     }
   if(!emergency) g_emergency_event_announced = false;
  }

ENUM_GUARDIAN_DECISION GuardianEvaluateOpen(string symbol, double planned_risk_usd, ENUM_ORDER_TYPE order_type, string &block_reason) 
  {
   block_reason=""; 
   GuardianHeartbeat(); 
   if(g_snap.state == GUARDIAN_LOCKED || g_snap.state == GUARDIAN_FORCE_CLOSE) { block_reason="GUARDIAN_STATE"; return DECISION_BLOCK; }
   if(!g_prop_execution_authorized) { block_reason="PROP_FIRM:"+g_prop_execution_reason; return DECISION_BLOCK; }
   string news_reason="";
   if(IsNewsEntryBlocked(symbol,news_reason)) { block_reason="NEWS:"+news_reason; NotifyEvent("NEWS_ENTRY_BLOCKED",news_reason); return DECISION_BLOCK; }
   g_account_cooldown_to = GetAccountCooldownUntil();
   g_consecutive_losses = GetAccountConsecutiveLosses();
   if(g_account_cooldown_to > TimeCurrent())
     {
      block_reason=StringFormat("COOLDOWN_LOSSES until %s (streak %d)", TimeToString(g_account_cooldown_to, TIME_DATE|TIME_MINUTES), g_consecutive_losses);
      return DECISION_BLOCK;
     }
   // Cooldown crypto isolé (ne bloque pas EURUSD etc.)
   if(IsSymbolCrypto(symbol))
     {
      if(g_crypto_cooldown_to == 0 && GlobalVariableCheck(AccountStateGV("CRYPTO_CD")))
         g_crypto_cooldown_to = (datetime)GlobalVariableGet(AccountStateGV("CRYPTO_CD"));
      if(g_crypto_cooldown_to > TimeCurrent())
        {
         block_reason=StringFormat("CRYPTO_COOLDOWN until %s (streak %d)", TimeToString(g_crypto_cooldown_to, TIME_DATE|TIME_MINUTES), g_consecutive_losses);
         return DECISION_BLOCK;
        }
     }
   double stop_new_pct=(IsTightOneStepProfile() ? MathMin(InpStopNewTradesDailyPct,2.50) : InpStopNewTradesDailyPct);
   if(InpEnableDailyRiskScaling && g_detected_base_cap > 0.0 && (g_snap.daily_loss / g_detected_base_cap) * 100.0 >= stop_new_pct) { block_reason="DAILY_DRAWDOWN_STOP"; return DECISION_BLOCK; }
   if(IsComplianceRolloverWindow()) { block_reason="ROLLOVER"; return DECISION_BLOCK; } 
   if(!IsInAllowedSession()) { block_reason="SESSION"; return DECISION_BLOCK; }
   if(InpMaxAccountPositions > 0 && g_snap.total_account_positions >= InpMaxAccountPositions) { block_reason="MAX_ACCOUNT_POSITIONS"; return DECISION_BLOCK; } 
   // Forex / non-crypto: limite symbole globale (0 = illimité, comportement historique)
   bool is_crypto_sym = IsSymbolCrypto(symbol);
   int max_sym = InpMaxSymbolPositions;
   if(is_crypto_sym && InpMaxSymbolPositionsCrypto > 0)
      max_sym = InpMaxSymbolPositionsCrypto;
   if(max_sym > 0 && g_snap.symbol_positions >= max_sym) { block_reason="MAX_SYMBOL_POSITIONS"; return DECISION_BLOCK; }
   if(is_crypto_sym)
     {
      if(InpMaxCryptoSameDir > 0 && CountSymbolPositionsDir(symbol, order_type) >= InpMaxCryptoSameDir)
        { block_reason="MAX_CRYPTO_SAME_DIR"; return DECISION_BLOCK; }
      if(InpMaxCryptoOpenTotal > 0 && CountCryptoAccountPositions() >= InpMaxCryptoOpenTotal)
        { block_reason="MAX_CRYPTO_OPEN_TOTAL"; return DECISION_BLOCK; }
      RefreshCryptoDailyTradeCounter();
      if(InpMaxTradesPerDayCrypto > 0 && g_crypto_trades_today >= InpMaxTradesPerDayCrypto)
        { block_reason="MAX_CRYPTO_TRADES_DAY"; return DECISION_BLOCK; }
     }
   
   double max_allowed_open_risk = g_detected_base_cap * (InpMaxOpenAccountRiskPct / 100.0); 
   if((g_snap.open_risk_usd + planned_risk_usd) > max_allowed_open_risk) { block_reason="OPEN_RISK"; return DECISION_BLOCK; } 
   if(planned_risk_usd > g_snap.daily_remaining_guardian) { block_reason="DAILY_REMAINING"; return DECISION_BLOCK; } 
   if(planned_risk_usd > g_snap.overall_remaining_guardian) { block_reason="OVERALL_REMAINING"; return DECISION_BLOCK; } 
   
   return DECISION_ALLOW; 
  }

//+------------------------------------------------------------------+
//| NEWS GUARD FTMO - CALENDRIER ECONOMIQUE NATIF MT5               |
//+------------------------------------------------------------------+
string NewsUpper(string text)
  {
   StringToUpper(text);
   return text;
  }

bool NewsContains(string text, string needle)
  {
   return (StringFind(NewsUpper(text), NewsUpper(needle)) >= 0);
  }

bool IsCryptoSymbolName(string symbol)
  {
   string u=NewsUpper(symbol);
   return (StringFind(u,"BTC")>=0 || StringFind(u,"ETH")>=0 || StringFind(u,"SOL")>=0 ||
           StringFind(u,"XRP")>=0 || StringFind(u,"ADA")>=0 || StringFind(u,"DOGE")>=0 ||
           StringFind(u,"LTC")>=0 || StringFind(u,"BNB")>=0 || StringFind(u,"CRYPTO")>=0);
  }

bool IsOilSymbolName(string symbol)
  {
   string u=NewsUpper(symbol);
   return (StringFind(u,"USOIL")>=0 || StringFind(u,"UKOIL")>=0 || StringFind(u,"XTI")>=0 ||
           StringFind(u,"XBR")>=0 || StringFind(u,"WTI")>=0 || StringFind(u,"BRENT")>=0 ||
           StringFind(u,"OIL")>=0);
  }

bool IsGoldSymbolName(string symbol)
  {
   string u=NewsUpper(symbol);
   return (StringFind(u,"XAU")>=0 || StringFind(u,"GOLD")>=0);
  }

bool IsUSIndexSymbolName(string symbol)
  {
   string u=NewsUpper(symbol);
   return (StringFind(u,"US30")>=0 || StringFind(u,"US500")>=0 || StringFind(u,"US100")>=0 ||
           StringFind(u,"US2000")>=0 || StringFind(u,"NAS")>=0 || StringFind(u,"SPX")>=0 ||
           StringFind(u,"DXY")>=0);
  }

bool GetEventCurrency(ulong event_id, string &currency)
  {
   currency="";
   MqlCalendarEvent ev;
   if(!CalendarEventById(event_id,ev)) return false;
   MqlCalendarCountry country;
   if(!CalendarCountryById(ev.country_id,country)) return false;
   currency=country.currency;
   return true;
  }

bool IsFTMORestrictedEvent(ulong event_id, string &currency, string &event_name)
  {
   currency=""; event_name="";
   MqlCalendarEvent ev;
   if(!CalendarEventById(event_id,ev)) return false;
   event_name=ev.name;
   if(!GetEventCurrency(event_id,currency)) return false;
   string n=NewsUpper(ev.name+" "+ev.event_code);

   if(currency=="USD")
     return NewsContains(n,"FEDERAL FUNDS") || NewsContains(n,"FOMC") ||
            NewsContains(n,"NON-FARM") || NewsContains(n,"NONFARM") ||
            NewsContains(n,"UNEMPLOYMENT RATE") || NewsContains(n,"WAGES") ||
            NewsContains(n,"AVERAGE HOURLY") || NewsContains(n,"ADVANCE GDP") ||
            NewsContains(n,"GDP Q/Q") || NewsContains(n,"GDP QOQ") ||
            NewsContains(n,"CPI Y/Y") || NewsContains(n,"CPI YOY") ||
            NewsContains(n,"CONSUMER PRICE") || NewsContains(n,"IPC");
   if(currency=="EUR")
     return NewsContains(n,"MAIN REFINANCING") || NewsContains(n,"REFINANCING RATE");
   if(currency=="GBP")
     return NewsContains(n,"OFFICIAL BANK RATE") || NewsContains(n,"MPC VOTES") ||
            NewsContains(n,"CPI Y/Y") || NewsContains(n,"CPI YOY") || NewsContains(n,"CONSUMER PRICE");
   if(currency=="CAD")
     return NewsContains(n,"OVERNIGHT RATE") || NewsContains(n,"BOC RATE") ||
            NewsContains(n,"CPI M/M") || NewsContains(n,"EMPLOYMENT CHANGE") ||
            NewsContains(n,"UNEMPLOYMENT RATE");
   if(currency=="AUD")
     return NewsContains(n,"CASH RATE") || NewsContains(n,"RBA") ||
            NewsContains(n,"EMPLOYMENT CHANGE") || NewsContains(n,"UNEMPLOYMENT RATE") ||
            NewsContains(n,"CPI M/M") || NewsContains(n,"CPI M/Y") || NewsContains(n,"CPI Y/Y") ||
            NewsContains(n,"GDP Q/Q") || NewsContains(n,"GDP QOQ");
   if(currency=="NZD")
     return NewsContains(n,"OFFICIAL CASH RATE") || NewsContains(n,"RBNZ") ||
            NewsContains(n,"EMPLOYMENT CHANGE") || NewsContains(n,"UNEMPLOYMENT RATE") ||
            NewsContains(n,"CPI Q/Q") || NewsContains(n,"GDP Q/Q") || NewsContains(n,"GDP QOQ");
   if(currency=="CHF")
     return NewsContains(n,"SNB POLICY RATE") || NewsContains(n,"POLICY RATE");
   return false;
  }

bool IsFTMORestrictedForSymbol(string symbol, ulong event_id, string event_name, string currency);

bool IsFundedNextHighImpactEvent(ulong event_id,string &currency,string &event_name)
  {
   currency=""; event_name="";
   MqlCalendarEvent ev;
   if(!CalendarEventById(event_id,ev)) return false;
   if(ev.importance!=CALENDAR_IMPORTANCE_HIGH) return false;
   event_name=ev.name;
   GetEventCurrency(event_id,currency);
   return true;
  }

bool IsComplianceNewsEvent(ulong event_id,string &currency,string &event_name)
  {
   if(g_prop_news_policy==PROP_NEWS_FTMO_RESTRICTED_2MIN) return IsFTMORestrictedEvent(event_id,currency,event_name);
   if(g_prop_news_policy==PROP_NEWS_FUNDEDNEXT_REWARD_5MIN) return IsFundedNextHighImpactEvent(event_id,currency,event_name);
   return false;
  }

bool IsGenericHighImpactForSymbol(string symbol,string currency)
  {
   if(IsCryptoSymbolName(symbol)) return false;
   string base="",quote="";
   if(GetSymbolCurrencies(symbol,base,quote)) return (currency==base || currency==quote);
   if(IsGoldSymbolName(symbol) || IsOilSymbolName(symbol) || IsUSIndexSymbolName(symbol)) return currency=="USD";
   return false;
  }

bool IsComplianceNewsForSymbol(string symbol,ulong event_id,string event_name,string currency)
  {
   if(g_prop_news_policy==PROP_NEWS_FTMO_RESTRICTED_2MIN) return IsFTMORestrictedForSymbol(symbol,event_id,event_name,currency);
   if(g_prop_news_policy==PROP_NEWS_FUNDEDNEXT_REWARD_5MIN) return IsGenericHighImpactForSymbol(symbol,currency);
   return false;
  }

int ComplianceNewsWindowSeconds()
  {
   if(g_prop_news_policy==PROP_NEWS_FTMO_RESTRICTED_2MIN) return 120;
   if(g_prop_news_policy==PROP_NEWS_FUNDEDNEXT_REWARD_5MIN) return 300;
   return 0;
  }

bool ComplianceNewsProtectionEnabled()
  {
   if(g_prop_news_policy==PROP_NEWS_NONE) return false;
   if(g_prop_news_policy==PROP_NEWS_FUNDEDNEXT_REWARD_5MIN) return InpProtectFundedNextFundedNewsWindow;
   return true;
  }

bool IsFTMORestrictedForSymbol(string symbol, ulong event_id, string event_name, string currency)
  {
   if(IsCryptoSymbolName(symbol)) return false; // FTMO news table ne cible pas les cryptos
   string u=NewsUpper(symbol);
   if(IsOilSymbolName(symbol))
      return NewsContains(event_name,"CRUDE OIL INVENTORIES") || NewsContains(event_name,"OIL INVENTORIES") || NewsContains(event_name,"EIA");

   bool is_forex = (StringLen(SymbolInfoString(symbol,SYMBOL_CURRENCY_BASE))>0 &&
                    StringLen(SymbolInfoString(symbol,SYMBOL_CURRENCY_PROFIT))>0);
   bool usd_target = (IsGoldSymbolName(symbol) || IsUSIndexSymbolName(symbol) ||
                      StringFind(u,"USD")>=0 || currency=="USD");
   if(IsGoldSymbolName(symbol) || IsUSIndexSymbolName(symbol)) return (currency=="USD");
   if(is_forex)
     {
      string base=SymbolInfoString(symbol,SYMBOL_CURRENCY_BASE);
      string profit=SymbolInfoString(symbol,SYMBOL_CURRENCY_PROFIT);
      return (currency==base || currency==profit);
     }
   return usd_target && currency=="USD";
  }

void AddNewsCache(datetime t, ulong event_id, string currency, string name)
  {
   for(int i=0;i<ArraySize(g_news_cache);i++)
      if(g_news_cache[i].time==t && g_news_cache[i].event_id==event_id) return;
   int n=ArraySize(g_news_cache);
   ArrayResize(g_news_cache,n+1);
   g_news_cache[n].time=t; g_news_cache[n].event_id=event_id;
   g_news_cache[n].currency=currency; g_news_cache[n].name=name;
  }

bool NewsCsvAlreadyContains(datetime t, ulong event_id)
  {
   int h=FileOpen(g_news_csv_name,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(h==INVALID_HANDLE) return false;
   if(!FileIsEnding(h)) { FileReadString(h); FileReadString(h); FileReadString(h); FileReadString(h); }
   while(!FileIsEnding(h))
     {
      string st=FileReadString(h); string sid=FileReadString(h);
      FileReadString(h); FileReadString(h);
      if(st!="" && (datetime)StringToInteger(st)==t && (ulong)StringToInteger(sid)==event_id) { FileClose(h); return true; }
     }
   FileClose(h);
   return false;
  }

void ExportNewsEvent(datetime t, ulong event_id, string currency, string name)
  {
   if(!InpExportNewsCalendar) return;
   if(NewsCsvAlreadyContains(t,event_id)) return;
   int h=FileOpen(g_news_csv_name,FILE_READ|FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(h==INVALID_HANDLE) return;
   if(FileSize(h)==0) FileWrite(h,"time","event_id","currency","name");
   FileSeek(h,0,SEEK_END);
   FileWrite(h,(long)t,(long)event_id,currency,name);
   FileClose(h);
  }

void RefreshNativeNewsCalendar()
  {
   if(!InpEnableFTMONewsGuard) return;
   if(g_prop_news_policy==PROP_NEWS_NONE) { ArrayResize(g_news_cache,0); return; }
   datetime now=TimeTradeServer();
   if(now<=0) now=TimeCurrent();
   if(g_news_cache_last_refresh!=0 && (now-g_news_cache_last_refresh)<60) return;
   g_news_cache_last_refresh=now;

   ArrayResize(g_news_cache,0);
   if(MQLInfoInteger(MQL_TESTER))
     {
      if(!InpUseNewsHistoryInTester) return;
      int h=FileOpen(g_news_csv_name,FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
      if(h==INVALID_HANDLE) return;
      if(!FileIsEnding(h)) FileReadString(h);
      if(!FileIsEnding(h)) FileReadString(h);
      if(!FileIsEnding(h)) FileReadString(h);
      if(!FileIsEnding(h)) FileReadString(h);
      while(!FileIsEnding(h))
        {
         string st=FileReadString(h); if(FileIsEnding(h) && st=="") break;
         string sid=FileReadString(h); string cur=FileReadString(h); string name=FileReadString(h);
         if(st=="") continue;
         AddNewsCache((datetime)StringToInteger(st),(ulong)StringToInteger(sid),cur,name);
        }
      FileClose(h);
      return;
     }

   MqlCalendarValue values[];
   datetime to=now + InpNewsLookAheadHours*3600;
   int count=CalendarValueHistory(values,now-3600,to);
   if(count<=0) return;
   for(int i=0;i<count;i++)
     {
      ulong eid=values[i].event_id;
      string cur="", name="";
      if(!IsComplianceNewsEvent(eid,cur,name)) continue;
      AddNewsCache(values[i].time,eid,cur,name);
      ExportNewsEvent(values[i].time,eid,cur,name);
     }
  }

bool GetNearestRestrictedNews(string symbol, datetime now, datetime &event_time, ulong &event_id, string &event_name, string &currency)
  {
   event_time=0; event_id=0; event_name=""; currency="";
   RefreshNativeNewsCalendar();
   long best=2147483647;
   for(int i=0;i<ArraySize(g_news_cache);i++)
     {
      datetime t=g_news_cache[i].time;
      if(t<now-InpPreNewsCloseMinutes*60 || t>now+InpNewsLookAheadHours*3600) continue;
      if(!IsComplianceNewsForSymbol(symbol,g_news_cache[i].event_id,g_news_cache[i].name,g_news_cache[i].currency)) continue;
      long d=MathAbs((long)(t-now));
      if(d<best) { best=d; event_time=t; event_id=g_news_cache[i].event_id; event_name=g_news_cache[i].name; currency=g_news_cache[i].currency; }
     }
   return event_time>0;
  }

bool IsFTMONewsLockActive(string symbol, datetime now, datetime &event_time, ulong &event_id, string &event_name, string &currency)
  {
   event_time=0; event_id=0; event_name=""; currency="";
   RefreshNativeNewsCalendar();
   for(int i=0;i<ArraySize(g_news_cache);i++)
     {
      datetime t=g_news_cache[i].time;
      int window=ComplianceNewsWindowSeconds();
      if(window<=0 || t>now+window || t<now-window) continue;
      if(IsComplianceNewsForSymbol(symbol,g_news_cache[i].event_id,g_news_cache[i].name,g_news_cache[i].currency))
        { event_time=t; event_id=g_news_cache[i].event_id; event_name=g_news_cache[i].name; currency=g_news_cache[i].currency; return true; }
     }
   return false;
  }

void CheckFTMONewsProtection()
  {
   if(!InpEnableFTMONewsGuard || !ComplianceNewsProtectionEnabled()) return;
   datetime now=TimeTradeServer(); if(now<=0) now=TimeCurrent();
   datetime et=0; ulong eid=0; string en="", cur="";
   bool locked=IsFTMONewsLockActive(_Symbol,now,et,eid,en,cur);
   if(locked) return;
   if(!InpCloseBeforeRestrictedNews) return;
   if(!GetNearestRestrictedNews(_Symbol,now,et,eid,en,cur)) return;
   long secs=(long)(et-now);
   if(secs<0 || secs>(long)InpPreNewsCloseMinutes*60) return;
   if(g_news_last_action_event==eid && MathAbs((long)(now-g_news_last_action_time))<30) return;
   g_news_last_action_event=eid; g_news_last_action_time=now;
   NotifyEvent("NEWS_PRE_CLOSE",StringFormat("🔔 %s | %s | T-%d s | fermeture preventive %d min avant | symbole %s",cur,en,(int)secs,InpPreNewsCloseMinutes,_Symbol));
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i); if(ticket==0) continue;
      string sym=PositionGetString(POSITION_SYMBOL);
      if(sym!=_Symbol) continue;
      if(!IsComplianceNewsForSymbol(sym,eid,en,cur)) continue;
      if(!g_trade.PositionClose(ticket))
         NotifyEvent("NEWS_PRE_CLOSE_FAILED",StringFormat("#%I64u | %s | %s",ticket,sym,g_trade.ResultRetcodeDescription()));
      else
         NotifyEvent("NEWS_POSITION_CLOSED",StringFormat("#%I64u | %s | %s | avant %s",ticket,sym,en));
     }
  }

bool IsNewsEntryBlocked(string symbol, string &reason)
  {
   reason="";
   if(!InpEnableFTMONewsGuard || !InpBlockRestrictedNews || g_prop_news_policy==PROP_NEWS_NONE) return false;
   datetime now=TimeTradeServer(); if(now<=0) now=TimeCurrent();
   datetime et=0; ulong eid=0; string en="", cur="";
   if(IsFTMONewsLockActive(symbol,now,et,eid,en,cur))
     { reason=StringFormat("[NEWS][%s] %s %s | %s",PropFirmName(g_prop_firm),cur,en,PropNewsPolicyName()); return true; }
   return false;
  }

void CheckFridayWeekendProtection()
  {
   if(!InpCloseForexOnFriday || IsSymbolCrypto(_Symbol)) return;
   datetime gmt = (MQLInfoInteger(MQL_TESTER) ? TimeCurrent() : TimeGMT());
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   
   if(dt.day_of_week == 5 && dt.hour >= 20)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) == _Symbol)
           {
            g_trade.PositionClose(ticket);
            PrintFormat("🛑 [WEEKEND SHIELD] Clôture préventive vendredi 20h GMT sur %s (#%I64u)", _Symbol, ticket);
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| GESTION POSITION ET RUNNERS (ASYMETRIE DE PROFIT)                |
//+------------------------------------------------------------------+
double GetStrategyATRValue(ENUM_AUTO_STRATEGY strategy)
  {
   // Le handle de setup est créé une seule fois dans OnInit. Recréer puis
   // libérer ici un ATR aux mêmes paramètres peut invalider le handle partagé.
   if(g_atr_handle==INVALID_HANDLE) return 0.0;
   double a[]; ArraySetAsSeries(a,true);
   double v=0.0;
   if(CopyBuffer(g_atr_handle,0,1,1,a)>0) v=a[0];
   return v;
  }


bool ManageManualPositionUnlocked(ulong ticket,bool immediate_attempt=false)
  {
   if(!InpAdoptManualTrades || !PositionSelectByTicket(ticket)) return false;
   if(PositionGetInteger(POSITION_MAGIC)!=0) return false;

   string symbol=PositionGetString(POSITION_SYMBOL);
   if(symbol=="") return false;
   SymbolSelect(symbol,true);

   ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   ulong state_id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   if(state_id==0) state_id=ticket;
   SetTicketState("MANUAL",state_id,1.0);
   double detect_ms=0.0;
   if(!GetTicketState("DETECTMS",state_id,detect_ms) || detect_ms<=0.0)
     {
      detect_ms=(double)GetTickCount64();
      SetTicketState("DETECTMS",state_id,detect_ms);
     }

   double open_price=PositionGetDouble(POSITION_PRICE_OPEN);
   double sl=PositionGetDouble(POSITION_SL);
   double tp=PositionGetDouble(POSITION_TP);
   double volume=PositionGetDouble(POSITION_VOLUME);
   MqlTick tick;
   if(!SymbolInfoTick(symbol,tick) || tick.bid<=0.0 || tick.ask<=0.0) return false;
   double current_price=(type==POSITION_TYPE_BUY ? tick.bid : tick.ask);

   double initial_risk_dist=0.0;
   bool have_risk=GetTicketState("RISK",state_id,initial_risk_dist) && initial_risk_dist>0.0;

   if(!have_risk && sl>0.0)
     {
      if(!IsManualSLTechnicallySafe(symbol,type,sl))
        {
         NotifyEvent("MANUAL_SL_TOO_CLOSE",StringFormat("#%I64u | %s | SL %.5f trop proche/invalide | fermeture securite",ticket,symbol,sl));
         if(!g_trade.PositionClose(ticket))
            PrintFormat("[MANUAL_SL_TOO_CLOSE] %s #%I64u | %s",symbol,ticket,g_trade.ResultRetcodeDescription());
         return false;
        }

      initial_risk_dist=MathAbs(open_price-sl);
      SetTicketState("RISK",state_id,initial_risk_dist);
      SetTicketState("RISKUSD",state_id,CalculateOpenPositionRisk(ticket));
      SetTicketState("TP1",state_id,0.0);
      SetTicketState("BE",state_id,0.0);
      SetTicketState("MTP",state_id,0.0);
      SetTicketState("MFE",state_id,0.0);
      SetTicketState("MAE",state_id,0.0);
      have_risk=true;
      NotifyEvent("MANUAL_ADOPTED",StringFormat("#%I64u | %s | %s %.2f lot @ %.5f | SL existant %.5f | TP %.5f | risque %.2f$ | %s",
                  ticket,symbol,(type==POSITION_TYPE_BUY?"BUY":"SELL"),volume,open_price,sl,tp,CalculateOpenPositionRisk(ticket),ManualAssetClassTag(symbol)));
     }

   if(!have_risk && sl<=0.0 && InpAutoAddSLToManual)
     {
      double atr_value=0.0;
      if(!GetManualATRValue(symbol,atr_value) || atr_value<=0.0)
        {
         double pending_ms=(double)GetTickCount64()-detect_ms;
         if(pending_ms>=MathMax(500,InpManualProtectionFailCloseMs))
           {
            NotifyEvent("MANUAL_UNPROTECTED",StringFormat("#%I64u | %s | ATR indisponible depuis %.0f ms | fermeture securite",ticket,symbol,pending_ms));
            if(!g_trade.PositionClose(ticket))
               PrintFormat("[MANUAL_UNPROTECTED] %s #%I64u | fermeture secours echouee: %s",symbol,ticket,g_trade.ResultRetcodeDescription());
            return false;
           }
         if(immediate_attempt)
            PrintFormat("[MANUAL_PROTECT_PENDING] %s #%I64u | ATR indisponible | retry timer | %.0f ms",symbol,ticket,pending_ms);
         return false;
        }

      double auto_sl_dist=atr_value*ManualSL_ATR_MultFor(symbol);
      double calculated_sl=(type==POSITION_TYPE_BUY ? open_price-auto_sl_dist : open_price+auto_sl_dist);
      calculated_sl=NormalizeTradePrice(symbol,calculated_sl);

      if(!IsManualSLTechnicallySafe(symbol,type,calculated_sl))
        {
         NotifyEvent("MANUAL_SL_REJECTED",StringFormat("#%I64u | %s | SL auto %.5f invalide | fermeture securite",ticket,symbol,calculated_sl));
         if(!g_trade.PositionClose(ticket))
            PrintFormat("[MANUAL_SL_REJECTED] %s #%I64u | %s",symbol,ticket,g_trade.ResultRetcodeDescription());
         return false;
        }

      // v11.15 : on conserve un TP déjà posé par l'utilisateur, mais on n'ajoute
      // JAMAIS un TP broker intégral automatiquement. Le TP partiel est logiciel.
      if(g_trade.PositionModify(ticket,calculated_sl,tp))
        {
         sl=calculated_sl;
         initial_risk_dist=MathAbs(open_price-sl);
         SetTicketState("RISK",state_id,initial_risk_dist);
         SetTicketState("RISKUSD",state_id,CalculateOpenPositionRisk(ticket));
         SetTicketState("TP1",state_id,0.0);
         SetTicketState("BE",state_id,0.0);
         SetTicketState("MTP",state_id,0.0);
         SetTicketState("MFE",state_id,0.0);
         SetTicketState("MAE",state_id,0.0);
         have_risk=true;

         GetTicketState("DETECTMS",state_id,detect_ms);
         double latency_ms=(detect_ms>0.0 ? (double)GetTickCount64()-detect_ms : -1.0);
         NotifyEvent("MANUAL_SL_ADDED",StringFormat("#%I64u | %s | %s | entree %.5f | SL %.5f | ATR %.5f x %.2f | risque %.2f$",
                     ticket,symbol,(type==POSITION_TYPE_BUY?"BUY":"SELL"),open_price,sl,atr_value,ManualSL_ATR_MultFor(symbol),CalculateOpenPositionRisk(ticket)));
         NotifyEvent("MANUAL_SL_CONFIRMED",StringFormat("#%I64u | %s | SL %.5f | latence detection->confirmation %.0f ms",ticket,symbol,sl,latency_ms));
        }
      else
        {
         NotifyEvent("MANUAL_SL_REJECTED",StringFormat("#%I64u | %s | %s",ticket,symbol,g_trade.ResultRetcodeDescription()));
         if(!g_trade.PositionClose(ticket))
            PrintFormat("[MANUAL_SL_REJECTED] %s #%I64u | fermeture secours echouee: %s",symbol,ticket,g_trade.ResultRetcodeDescription());
         return false;
        }
     }

   if(!have_risk)
     {
      if(!InpAutoAddSLToManual)
        {
         NotifyEvent("MANUAL_UNPROTECTED",StringFormat("#%I64u | %s | aucun SL exploitable et ajout auto desactive | fermeture securite",ticket,symbol));
         if(!g_trade.PositionClose(ticket))
            PrintFormat("[MANUAL_UNPROTECTED] %s #%I64u | %s",symbol,ticket,g_trade.ResultRetcodeDescription());
        }
      return false;
     }

   double risk_before_cap=CalculateOpenPositionRisk(ticket);
   if(risk_before_cap>GetManualMaxRiskUSD()+0.01)
     {
      if(!ReduceManualRiskToCap(ticket,GetManualMaxRiskUSD()))
        {
         if(PositionSelectByTicket(ticket))
           {
            NotifyEvent("MANUAL_RISK_UNSAFE",StringFormat("#%I64u | %s | impossible de ramener le risque sous %.2f$ | fermeture securite",ticket,symbol,GetManualMaxRiskUSD()));
            if(!g_trade.PositionClose(ticket))
               PrintFormat("[MANUAL_RISK_UNSAFE] %s #%I64u | %s",symbol,ticket,g_trade.ResultRetcodeDescription());
           }
         return false;
        }
      if(!PositionSelectByTicket(ticket)) return true;
      volume=PositionGetDouble(POSITION_VOLUME);
     }

   double mfe_dist=0.0,mae_dist=0.0;
   GetTicketState("MFE",state_id,mfe_dist);
   GetTicketState("MAE",state_id,mae_dist);
   double current_dist=(type==POSITION_TYPE_BUY ? current_price-open_price : open_price-current_price);
   if(current_dist>mfe_dist) SetTicketState("MFE",state_id,current_dist);
   if(current_dist<mae_dist) SetTicketState("MAE",state_id,current_dist);

   double profit_r=current_dist/initial_risk_dist;
   double be_status=0.0,tp_status=0.0;
   GetTicketState("BE",state_id,be_status);
   GetTicketState("MTP",state_id,tp_status);

   if(profit_r>=ManualBE_Trigger_R_For(symbol) && be_status<0.5)
     {
      double out_cost_points=0.0;
      double true_be_price=GetTrueBreakEvenPrice(ticket,open_price,type,volume,out_cost_points);
      bool improves=(sl<=0.0 || (type==POSITION_TYPE_BUY ? true_be_price>sl : true_be_price<sl));
      if(improves && IsStopLossValidForPosition(symbol,type,true_be_price))
        {
         if(g_trade.PositionModify(ticket,true_be_price,tp))
           {
            SetTicketState("BE",state_id,1.0);
            sl=true_be_price;
            NotifyEvent("MANUAL_BE",StringFormat("#%I64u | %s | +%.2fR | SL -> %.5f | BE + frais",ticket,symbol,profit_r,true_be_price));
           }
         else
            NotifyEvent("MANUAL_BE_FAILED",StringFormat("#%I64u | %s | %s",ticket,symbol,g_trade.ResultRetcodeDescription()));
        }
     }

   // TP logiciel partiel; aucun TP broker intégral n'est créé par Guardian.
   if(tp_status<0.5 && profit_r>=ManualTP_R_For(symbol))
     {
      double step_vol=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
      double min_vol=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
      double close_pct=ManualTP_ClosePctFor(symbol);
      double close_vol=MathFloor((volume*(close_pct/100.0))/step_vol+1e-9)*step_vol;
      close_vol=NormalizeDouble(close_vol,VolumeDigits(symbol));
      if(close_vol>=min_vol && (volume-close_vol)>=min_vol)
        {
         if(g_trade.PositionClosePartial(ticket,close_vol))
           {
            SetTicketState("MTP",state_id,1.0);
            double tp_price=ManualTPPrice(ticket,open_price,type,initial_risk_dist,volume);
            NotifyEvent("MANUAL_TP50",StringFormat("#%I64u | %s | +%.2fR | %.0f%% ferme (%.2f lot) | runner conserve | cible %.5f + frais",
                        ticket,symbol,profit_r,close_pct,close_vol,tp_price));
           }
         else
            NotifyEvent("MANUAL_TP50_FAILED",StringFormat("#%I64u | %s | %s",ticket,symbol,g_trade.ResultRetcodeDescription()));
        }
      else
        {
         SetTicketState("MTP",state_id,1.0);
         NotifyEvent("MANUAL_TP50_SKIPPED",StringFormat("#%I64u | %s | volume %.2f incompatible avec fermeture %.0f%%",ticket,symbol,volume,close_pct));
        }
     }

   return true;
  }

bool ManageManualPosition(ulong ticket,bool immediate_attempt=false)
  {
   if(!AcquireGlobalTradeLock(2)) return false;
   bool ok=ManageManualPositionUnlocked(ticket,immediate_attempt);
   ReleaseGlobalTradeLock();
   return ok;
  }

void ManageAccountWideManualTrades()
  {
   if(!InpAdoptManualTrades || !g_manual_guard_owner) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=0) continue;
      ManageManualPosition(ticket,false);
     }
  }

void ManagePositionsAndManualTrades()
  {
   // v11.15 : la couche manuelle est account-wide et appartient à une seule instance.
   if(InpAdoptManualTrades && g_manual_guard_owner)
      ManageAccountWideManualTrades();

   // Une seule instance par symbole gère les positions AUTO et les sorties.
   if(!g_symbol_instance_owner) return;

   bool is_rollover = IsPragueRolloverWindow();
   static datetime last_exit_diag_bar=0;
   datetime exit_diag_bar=iTime(_Symbol,GetProfileSetupTF("PORTFOLIO"),0);
   bool log_exit_snapshot=(InpEnableExitDebugLogs && exit_diag_bar>0 && exit_diag_bar!=last_exit_diag_bar);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      string pos_symbol=PositionGetString(POSITION_SYMBOL);
      if(pos_symbol != _Symbol) continue;

      long magic = PositionGetInteger(POSITION_MAGIC);
      bool is_our_auto = (magic == (long)g_auto_magic);
      if(!is_our_auto)
        {
         if(log_exit_snapshot)
            PrintFormat("[EXIT_DIAG] position ignored | ticket=%I64u symbol=%s magic=%I64d expected=%I64u",ticket,pos_symbol,magic,g_auto_magic);
         continue;
        }

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      ulong state_id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(state_id == 0) state_id = ticket;
      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double volume = PositionGetDouble(POSITION_VOLUME);
      double current_price = (type == POSITION_TYPE_BUY ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK));
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

      // ============================================================
      // AUTO : gestion spécifique à la stratégie.
      // ============================================================
      ENUM_AUTO_STRATEGY strategy=GetPositionStrategy(ticket,state_id);
      double initial_risk_dist=0.0;
      if(!GetTicketState("RISK",state_id,initial_risk_dist) || initial_risk_dist<=0.0)
        {
         if(sl>0.0) { initial_risk_dist=MathAbs(open_price-sl); SetTicketState("RISK",state_id,initial_risk_dist); SetTicketState("RISKUSD",state_id,CalculateOpenPositionRisk(ticket)); }
        }
      if(initial_risk_dist<=0.0) continue;

      double mfe_dist=0.0, mae_dist=0.0;
      GetTicketState("MFE",state_id,mfe_dist); GetTicketState("MAE",state_id,mae_dist);
      double current_dist=(type==POSITION_TYPE_BUY ? current_price-open_price : open_price-current_price);
      if(current_dist>mfe_dist) SetTicketState("MFE",state_id,current_dist);
      if(current_dist<mae_dist) SetTicketState("MAE",state_id,current_dist);
      double profit_r=current_dist/initial_risk_dist;
      double be_status=0.0,tp1_status=0.0;
      GetTicketState("BE",state_id,be_status); GetTicketState("TP1",state_id,tp1_status);

      double strategy_atr=GetStrategyATRValue(strategy);
      if(strategy_atr<=0.0)
        {
         if(log_exit_snapshot)
            PrintFormat("[EXIT_DIAG] position unmanaged | ticket=%I64u strategy=%s reason=STRATEGY_ATR_UNAVAILABLE profitR=%.3f",ticket,StrategyToString(strategy),profit_r);
         continue;
        }

      if(log_exit_snapshot)
         PrintFormat("[EXIT_DIAG] snapshot | ticket=%I64u strategy=%s side=%s open=%.5f price=%.5f SL=%.5f riskDist=%.5f profitR=%.3f BE=%.0f TP1=%.0f ATR=%.5f",
                     ticket,StrategyToString(strategy),(type==POSITION_TYPE_BUY?"BUY":"SELL"),open_price,current_price,sl,initial_risk_dist,profit_r,be_status,tp1_status,strategy_atr);

      if(IsMarketClosedBackoffActive())
        {
         if(log_exit_snapshot)
            PrintFormat("[EXIT_DIAG] actions deferred | ticket=%I64u reason=MARKET_CLOSED_BACKOFF retryAfter=%s profitR=%.3f",ticket,TimeToString(g_trade_session_backoff_until,TIME_DATE|TIME_MINUTES),profit_r);
         continue;
        }

      bool trade_session_known=false;
      bool trade_session_open=IsDeclaredTradeSessionOpen(_Symbol,TimeCurrent(),trade_session_known);
      if(trade_session_known && !trade_session_open)
        {
         if(log_exit_snapshot)
            PrintFormat("[EXIT_DIAG] actions deferred | ticket=%I64u reason=TRADE_SESSION_CLOSED profitR=%.3f",ticket,profit_r);
         continue;
        }

      int max_minutes=StrategyMaxMinutes(strategy);
      double min_progress=StrategyMinProgressR(strategy);
      datetime pos_open_time=(datetime)PositionGetInteger(POSITION_TIME);
      datetime time_stop_anchor=(pos_open_time<g_ea_start_time ? g_ea_start_time : pos_open_time);
      int managed_minutes=(int)MathMax(0,(long)(TimeCurrent()-time_stop_anchor)/60);
      // Crypto : pas de time-stop fixe. Toutes les autres classes conservent la règle.
      if(g_market_class!=MARKET_CRYPTO && InpEnableStrategyTimeStop && max_minutes>0 && managed_minutes >= max_minutes && profit_r<min_progress)
        {
         if(g_trade.PositionClose(ticket)) { NotifyEvent("STRATEGY_TIME_STOP",StringFormat("#%I64u | %s | %.2fR apres %d min de gestion",ticket,StrategyToString(strategy),profit_r,managed_minutes)); continue; }
         if(RegisterMarketClosedBackoff("STRATEGY_TIME_STOP",ticket)) continue;
        }

      // Sweep : la sortie structurelle est prioritaire et la gestion est rapide.
      if(strategy==STRAT_SWEEP)
        {
         double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP), minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
         double sweep_tp_r=(g_market_class==MARKET_CRYPTO ? InpCryptoSweepTP_R : InpSweepTP1_R);
         if(tp1_status<0.5 && profit_r>=sweep_tp_r)
           {
            double cv=MathFloor((volume*(InpSweepTP1_ClosePct/100.0))/step+1e-9)*step; cv=NormalizeDouble(cv,VolumeDigits(_Symbol));
            bool volume_compatible=(cv>=minv && volume-cv>=minv);
            bool partial_ok=(volume_compatible && g_trade.PositionClosePartial(ticket,cv));
            if(partial_ok) { SetTicketState("TP1",state_id,1.0); NotifyEvent("SWEEP_TP1",StringFormat("#%I64u | +%.2fR | %.0f%% ferme",ticket,profit_r,InpSweepTP1_ClosePct)); }
            else if(!volume_compatible)
              {
               SetTicketState("TP1",state_id,1.0);
               if(InpEnableExitDebugLogs)
                  PrintFormat("[EXIT_DIAG] SWEEP_TP1 skipped | ticket=%I64u volume=%.4f close=%.4f min=%.4f reason=VOLUME_INCOMPATIBLE",ticket,volume,cv,minv);
              }
            else
              {
               if(InpEnableExitDebugLogs)
                  PrintFormat("[EXIT_DIAG] SWEEP_TP1 failed | ticket=%I64u profitR=%.3f volume=%.4f close=%.4f min=%.4f retcode=%u %s",ticket,profit_r,volume,cv,minv,g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
               if(RegisterMarketClosedBackoff("SWEEP_TP1",ticket)) continue;
              }
           }
         if(profit_r>=InpSweepBE_R && be_status<0.5)
           {
            double c=0.0; double bep=GetTrueBreakEvenPrice(ticket,open_price,type,volume,c);
            bool improves=(sl<=0.0 || (type==POSITION_TYPE_BUY ? bep>sl : bep<sl));
            bool be_valid=(improves && IsStopLossValidForPosition(_Symbol,type,bep));
            bool be_ok=(be_valid && g_trade.PositionModify(ticket,bep,tp));
            if(be_ok) { SetTicketState("BE",state_id,1.0); sl=bep; }
            if(InpEnableExitDebugLogs)
               PrintFormat("[EXIT_DIAG] SWEEP_BE %s | ticket=%I64u profitR=%.3f requestedSL=%.5f improves=%s valid=%s retcode=%u %s",(be_ok?"OK":"FAILED"),ticket,profit_r,bep,(improves?"YES":"NO"),(be_valid?"YES":"NO"),g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
            if(be_valid && !be_ok && RegisterMarketClosedBackoff("SWEEP_BE",ticket)) continue;
           }
         if(profit_r>=InpSweepBE_R)
           {
            double d=strategy_atr*InpSweepTrailATR; double csl=(type==POSITION_TYPE_BUY ? current_price-d : current_price+d);
            bool trail_improves=(type==POSITION_TYPE_BUY ? csl>sl+5*point : csl<sl-5*point);
            bool trail_valid=(trail_improves && IsStopLossValidForPosition(_Symbol,type,csl));
            datetime trail_bar=iTime(_Symbol,GetProfileSetupTF("PORTFOLIO"),0);
            if(trail_bar<=0) trail_bar=(datetime)(((long)TimeCurrent()/300)*300);
            double last_trail_bar=0.0;
            GetTicketState("TRAILBAR",state_id,last_trail_bar);
            if(trail_valid && (datetime)last_trail_bar<trail_bar)
              {
               SetTicketState("TRAILBAR",state_id,(double)trail_bar);
               bool trail_ok=g_trade.PositionModify(ticket,NormalizeTradePrice(_Symbol,csl),tp);
               if(InpEnableExitDebugLogs && !trail_ok)
                  PrintFormat("[EXIT_DIAG] SWEEP_TRAIL failed | ticket=%I64u profitR=%.3f requestedSL=%.5f retcode=%u %s",ticket,profit_r,csl,g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
               if(!trail_ok && RegisterMarketClosedBackoff("SWEEP_TRAIL",ticket)) continue;
              }
           }
         continue;
        }

      if(strategy==STRAT_PULLBACK && tp1_status<0.5 && profit_r>=InpPullbackTP1_R)
        {
         double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP), minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
         double cv=MathFloor((volume*(InpPullbackTP1_ClosePct/100.0))/step+1e-9)*step; cv=NormalizeDouble(cv,VolumeDigits(_Symbol));
         bool volume_compatible=(cv>=minv && volume-cv>=minv);
         bool partial_ok=(volume_compatible && g_trade.PositionClosePartial(ticket,cv));
         if(partial_ok) { SetTicketState("TP1",state_id,1.0); NotifyEvent("PULLBACK_TP1",StringFormat("#%I64u | +%.2fR | %.0f%% ferme",ticket,profit_r,InpPullbackTP1_ClosePct)); }
         else if(!volume_compatible)
           {
            SetTicketState("TP1",state_id,1.0);
            if(InpEnableExitDebugLogs)
               PrintFormat("[EXIT_DIAG] PULLBACK_TP1 skipped | ticket=%I64u volume=%.4f close=%.4f min=%.4f reason=VOLUME_INCOMPATIBLE",ticket,volume,cv,minv);
           }
         else
           {
            if(InpEnableExitDebugLogs)
               PrintFormat("[EXIT_DIAG] PULLBACK_TP1 failed | ticket=%I64u profitR=%.3f volume=%.4f close=%.4f min=%.4f retcode=%u %s",ticket,profit_r,volume,cv,minv,g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
            if(RegisterMarketClosedBackoff("PULLBACK_TP1",ticket)) continue;
           }
        }

      if(strategy==STRAT_MOMENTUM && tp1_status<0.5 && profit_r>=InpMomentumTP1_R)
        {
         double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP), minv=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
         double cv=MathFloor((volume*(InpMomentumTP1_ClosePct/100.0))/step+1e-9)*step;
         cv=NormalizeDouble(cv,VolumeDigits(_Symbol));
         bool volume_compatible=(cv>=minv && volume-cv>=minv);
         bool partial_ok=(volume_compatible && g_trade.PositionClosePartial(ticket,cv));
         if(partial_ok)
           {
            SetTicketState("TP1",state_id,1.0);
            NotifyEvent("MOMENTUM_TP1",StringFormat("#%I64u | +%.2fR | %.0f%% ferme",ticket,profit_r,InpMomentumTP1_ClosePct));
           }
         else if(!volume_compatible)
           {
            SetTicketState("TP1",state_id,1.0);
            if(InpEnableExitDebugLogs)
               PrintFormat("[EXIT_DIAG] MOMENTUM_TP1 skipped | ticket=%I64u volume=%.4f close=%.4f min=%.4f reason=VOLUME_INCOMPATIBLE",ticket,volume,cv,minv);
           }
         else
           {
            if(InpEnableExitDebugLogs)
               PrintFormat("[EXIT_DIAG] MOMENTUM_TP1 failed | ticket=%I64u profitR=%.3f volume=%.4f close=%.4f min=%.4f retcode=%u %s",ticket,profit_r,volume,cv,minv,g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
            if(RegisterMarketClosedBackoff("MOMENTUM_TP1",ticket)) continue;
           }
        }

      double be_trigger=StrategyBE_R(strategy);
      double trail_atr=StrategyTrailATR(strategy);
      if(profit_r>=be_trigger && be_status<0.5)
        {
         double c=0.0; double bep=GetTrueBreakEvenPrice(ticket,open_price,type,volume,c);
         bool improves=(sl<=0.0 || (type==POSITION_TYPE_BUY ? bep>sl : bep<sl));
         bool be_valid=(improves && IsStopLossValidForPosition(_Symbol,type,bep));
         bool be_ok=(be_valid && g_trade.PositionModify(ticket,bep,tp));
         if(be_ok) { SetTicketState("BE",state_id,1.0); sl=bep; }
         if(InpEnableExitDebugLogs)
            PrintFormat("[EXIT_DIAG] %s_BE %s | ticket=%I64u profitR=%.3f requestedSL=%.5f improves=%s valid=%s retcode=%u %s",StrategyToString(strategy),(be_ok?"OK":"FAILED"),ticket,profit_r,bep,(improves?"YES":"NO"),(be_valid?"YES":"NO"),g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
         if(be_valid && !be_ok && RegisterMarketClosedBackoff(StrategyToString(strategy)+"_BE",ticket)) continue;
        }
      if(profit_r>=be_trigger)
        {
         double d=strategy_atr*trail_atr; double csl=(type==POSITION_TYPE_BUY ? current_price-d : current_price+d);
         bool trail_improves=(type==POSITION_TYPE_BUY ? csl>sl+5*point : csl<sl-5*point);
         bool trail_valid=(trail_improves && IsStopLossValidForPosition(_Symbol,type,csl));
         datetime trail_bar=iTime(_Symbol,GetProfileSetupTF("PORTFOLIO"),0);
         if(trail_bar<=0) trail_bar=(datetime)(((long)TimeCurrent()/300)*300);
         double last_trail_bar=0.0;
         GetTicketState("TRAILBAR",state_id,last_trail_bar);
         if(trail_valid && (datetime)last_trail_bar<trail_bar)
           {
            SetTicketState("TRAILBAR",state_id,(double)trail_bar);
            bool trail_ok=g_trade.PositionModify(ticket,NormalizeTradePrice(_Symbol,csl),tp);
            if(InpEnableExitDebugLogs && !trail_ok)
               PrintFormat("[EXIT_DIAG] %s_TRAIL failed | ticket=%I64u profitR=%.3f requestedSL=%.5f retcode=%u %s",StrategyToString(strategy),ticket,profit_r,csl,g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
            if(!trail_ok && RegisterMarketClosedBackoff(StrategyToString(strategy)+"_TRAIL",ticket)) continue;
           }
        }

      if(strategy==STRAT_BREAKOUT)
        {
         ENUM_TIMEFRAMES tf=GetProfileSetupTF("BREAKOUT");
         int p=InpBreakoutExitDonchian;
         if(p>1)
           {
            int opp=(type==POSITION_TYPE_BUY ? iLowest(_Symbol,tf,MODE_LOW,p,1) : iHighest(_Symbol,tf,MODE_HIGH,p,1));
            if(opp>=0)
              {
               double level=(type==POSITION_TYPE_BUY ? iLow(_Symbol,tf,opp) : iHigh(_Symbol,tf,opp));
               if((type==POSITION_TYPE_BUY && current_price<level) || (type==POSITION_TYPE_SELL && current_price>level))
                 {
                  if(g_trade.PositionClose(ticket)) NotifyEvent("BREAKOUT_STRUCTURE_EXIT",StringFormat("#%I64u | niveau %.5f",ticket,level));
                  else RegisterMarketClosedBackoff("BREAKOUT_STRUCTURE_EXIT",ticket);
                 }
              }
           }
        }
     }
   if(log_exit_snapshot) last_exit_diag_bar=exit_diag_bar;
  }

string StrategyToString(ENUM_AUTO_STRATEGY s)
  {
   switch(s) { case STRAT_BREAKOUT:return "BREAKOUT"; case STRAT_PULLBACK:return "PULLBACK"; case STRAT_SWEEP:return "SWEEP"; default:return "MOMENTUM"; }
  }

ENUM_AUTO_STRATEGY GetPositionStrategy(ulong ticket,ulong state_id)
  {
   double v=0.0;
   if(GetTicketState("STRAT",state_id,v)) return (ENUM_AUTO_STRATEGY)(int)v;
   string c=PositionGetString(POSITION_COMMENT);
   ENUM_AUTO_STRATEGY s=STRAT_BREAKOUT;
   if(StringFind(c,"Pullback")>=0) s=STRAT_PULLBACK;
   else if(StringFind(c,"Sweep")>=0) s=STRAT_SWEEP;
   else if(StringFind(c,"Momentum")>=0) s=STRAT_MOMENTUM;
   SetTicketState("STRAT",state_id,(double)s);
   return s;
  }

double StrategyBE_R(ENUM_AUTO_STRATEGY s)
  { if(s==STRAT_BREAKOUT)return InpBreakoutBE_R; if(s==STRAT_PULLBACK)return InpPullbackBE_R; if(s==STRAT_SWEEP)return InpSweepBE_R; return InpMomentumBE_R; }

double StrategyTrailATR(ENUM_AUTO_STRATEGY s)
  { if(s==STRAT_BREAKOUT)return InpBreakoutTrailATR; if(s==STRAT_PULLBACK)return InpPullbackTrailATR; if(s==STRAT_SWEEP)return InpSweepTrailATR; return InpMomentumTrailATR; }

int StrategyMaxMinutes(ENUM_AUTO_STRATEGY s)
  { if(s==STRAT_BREAKOUT)return InpBreakoutMaxMinutes; if(s==STRAT_PULLBACK)return InpPullbackMaxMinutes; if(s==STRAT_SWEEP)return InpSweepMaxMinutes; return InpMomentumMaxMinutes; }

double StrategyMinProgressR(ENUM_AUTO_STRATEGY s)
  { if(s==STRAT_BREAKOUT)return InpBreakoutMinProgressR; if(s==STRAT_PULLBACK)return InpPullbackMinProgressR; if(s==STRAT_SWEEP)return InpSweepMinProgressR; return InpMomentumMinProgressR; }

//+------------------------------------------------------------------+
//| EXECUTION ET SIGNAUX MULTI-TIMEFRAME                             |
//+------------------------------------------------------------------+
double EffectiveMinTradeRiskUSD()
  {
   double configured=(g_market_class==MARKET_CRYPTO ? InpMinTradeRiskUSDCrypto : InpMinTradeRiskUSD);
   if(!InpScaleMinTradeRiskWithCapital || g_detected_base_cap<=0.0) return configured;
   return configured*(g_detected_base_cap/100000.0);
  }

void ExecuteTrade(ENUM_ORDER_TYPE order_type, string engine_name, string reason_log, double sl_dist)
  {
   if(InpStrategyMode == MODE_MANUAL_GUARDIAN_ONLY)
     {
      NotifyEvent("ORDER_BLOCKED_MODE", StringFormat("%s | mode MANUAL_GUARDIAN_ONLY", engine_name));
      return;
     }
   if(!AcquireGlobalTradeLock(3))
     {
      NotifyEvent("ORDER_BLOCKED_LOCK", StringFormat("%s | mutex compte indisponible", engine_name));
      return;
     }

   if(IsMarketClosedBackoffActive())
     {
      NotifyEvent("ORDER_BLOCKED_SESSION",StringFormat("%s | reprise après %s",engine_name,TimeToString(g_trade_session_backoff_until,TIME_DATE|TIME_MINUTES)));
      ReleaseGlobalTradeLock();
      return;
     }

   bool trade_session_known=false;
   bool trade_session_open=IsDeclaredTradeSessionOpen(_Symbol,TimeCurrent(),trade_session_known);
   if(trade_session_known && !trade_session_open)
     {
      NotifyEvent("ORDER_BLOCKED_SESSION",StringFormat("%s | session de négociation fermée",engine_name));
      ReleaseGlobalTradeLock();
      return;
     }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double spread = ask - bid;

   // IMPORTANT: sl_dist vient du meme ATR/timeframe que celui utilise pour construire le signal.
   // On ne recalcule plus ici un ATR provenant du timeframe du graphique de l'EA.
   if(sl_dist <= 0.0 || point <= 0.0)
     {
      NotifyEvent("ORDER_BLOCKED_SL_CALC", StringFormat("%s | point %.10f | SLdist %.10f", engine_name, point, sl_dist));
      ReleaseGlobalTradeLock();
      return;
     }

   double spread_pct_of_sl = (spread / sl_dist) * 100.0;
   if(spread_pct_of_sl > InpMaxSpreadPercentOfSL)
     {
      NotifyEvent("ORDER_BLOCKED_SPREAD", StringFormat("%s | spread %.5f = %.1f%% du SL | max %.1f%%", engine_name, spread, spread_pct_of_sl, InpMaxSpreadPercentOfSL));
      ReleaseGlobalTradeLock();
      return;
     }

   double risk_percent_base = GetEffectiveRiskPercent();
   double risk_factor = (InpEnableSignalRanking ? g_selected_signal_risk_factor : 1.0);
   double risk_percent = risk_percent_base * risk_factor;
   if(risk_percent <= 0.0)
     {
      NotifyEvent("ORDER_BLOCKED_DRAWDOWN", StringFormat("%s | riskBase %.3f%% | factor %.2f | dailyLoss %.2f%%", engine_name, risk_percent_base, risk_factor, (g_detected_base_cap > 0.0 ? (g_snap.daily_loss / g_detected_base_cap) * 100.0 : 0.0)));
      ReleaseGlobalTradeLock();
      return;
     }

   double sl_points = sl_dist / point;
   double risk_usd_target = g_detected_base_cap * (risk_percent / 100.0);
   double lots = CalculateDynamicLot(_Symbol, sl_points, risk_percent, order_type);
   double min_trade_risk = EffectiveMinTradeRiskUSD();
   if(risk_usd_target + 1e-9 < min_trade_risk)
     {
      NotifyEvent("ORDER_BLOCKED_MIN_TRADE_RISK", StringFormat("%s | targetRisk %.2f$ < minimum %.2f$ | risk %.3f%% | capital %.2f$ | facteur %.2f", engine_name, risk_usd_target, min_trade_risk, risk_percent, g_detected_base_cap, risk_factor));
      ReleaseGlobalTradeLock();
      return;
     }
   double actual_trade_risk_usd = 0.0;
   bool actual_risk_ok = CalculateLossAtLot(_Symbol, sl_points, order_type, lots, actual_trade_risk_usd);
   if(actual_risk_ok && actual_trade_risk_usd + 1e-9 < min_trade_risk)
     {
      NotifyEvent("ORDER_BLOCKED_MIN_TRADE_RISK", StringFormat("%s | actualRisk %.2f$ < minimum %.2f$ | targetRisk %.2f$ | lots %.4f | SLdist %.5f", engine_name, actual_trade_risk_usd, min_trade_risk, risk_usd_target, lots, sl_dist));
      ReleaseGlobalTradeLock();
      return;
     }
   if(!actual_risk_ok) actual_trade_risk_usd = risk_usd_target;

   if(lots <= 0.0)
     {
      double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      double min_lot_loss=0.0;
      bool min_calc_ok=CalculateLossAtLot(_Symbol,sl_points,order_type,min_lot,min_lot_loss);
      double risk_ratio=(risk_usd_target>0.0 && min_calc_ok ? min_lot_loss/risk_usd_target : 0.0);
      NotifyEvent("ORDER_BLOCKED_MIN_LOT_RISK", StringFormat("%s | targetRisk %.2f$ | minLot %.4f | minLotRisk %.2f$ | ratio %.2fx | SLdist %.5f (%.1f pts) | calc %s | step %.4f | max %.4f | freeMargin %.2f$", engine_name, risk_usd_target, min_lot, min_lot_loss, risk_ratio, sl_dist, sl_points, (min_calc_ok ? "OK" : "FAIL"), step_lot, max_lot, AccountInfoDouble(ACCOUNT_MARGIN_FREE)));
      ReleaseGlobalTradeLock();
      return;
     }

   // Le risque demandé doit rester plafonné au budget calculé pour ce signal.
   double trade_risk_usd = (actual_risk_ok ? actual_trade_risk_usd : risk_usd_target);
   string guardian_block_reason="";
   if(GuardianEvaluateOpen(_Symbol, trade_risk_usd, order_type, guardian_block_reason) != DECISION_ALLOW)
     {
      NotifyEvent("ORDER_BLOCKED_GUARDIAN", StringFormat("%s | reason=%s | risque %.2f$ | openRisk %.2f$ | maxOpen %.2f$ | dailyRemain %.2f$ | overallRemain %.2f$ | positions %d/%s | symbolPos %d/%s", engine_name, guardian_block_reason, trade_risk_usd, g_snap.open_risk_usd, g_detected_base_cap * (InpMaxOpenAccountRiskPct / 100.0), g_snap.daily_remaining_guardian, g_snap.overall_remaining_guardian, g_snap.total_account_positions, (InpMaxAccountPositions>0?IntegerToString(InpMaxAccountPositions):"UNLIMITED"), g_snap.symbol_positions, (InpMaxSymbolPositions>0?IntegerToString(InpMaxSymbolPositions):"UNLIMITED")));
      ReleaseGlobalTradeLock();
      return;
     }

   double price = (order_type == ORDER_TYPE_BUY ? ask : bid);
   double sl = (order_type == ORDER_TYPE_BUY ? price - sl_dist : price + sl_dist);
   sl = NormalizeTradePrice(_Symbol, sl);
   ENUM_POSITION_TYPE position_type = (order_type == ORDER_TYPE_BUY ? POSITION_TYPE_BUY : POSITION_TYPE_SELL);
   if(!IsStopLossValidForPosition(_Symbol, position_type, sl))
     {
      NotifyEvent("ORDER_BLOCKED_INVALID_SL", StringFormat("%s | price %.5f | SL %.5f | dist %.5f | stopsLevel %d pts | freezeLevel %d pts", engine_name, price, sl, MathAbs(price-sl), (int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL), (int)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_FREEZE_LEVEL)));
      ReleaseGlobalTradeLock();
      return;
     }

   string comment = StringFormat("PRO11 [%s]", engine_name);
   NotifyEvent("ORDER_ATTEMPT", StringFormat("%s | %s | risk %.2f%% (%.2f$) | lots %.4f | SLdist %.5f | SL %.5f | spread %.5f | %s", engine_name, (order_type == ORDER_TYPE_BUY ? "BUY" : "SELL"), risk_percent, trade_risk_usd, lots, sl_dist, sl, spread, reason_log));

   bool ok = (order_type == ORDER_TYPE_BUY) ? g_trade.Buy(lots, _Symbol, price, sl, 0.0, comment) : g_trade.Sell(lots, _Symbol, price, sl, 0.0, comment);

   if(ok)
     {
      ulong deal_ticket = g_trade.ResultDeal();
      ulong pos_id = 0;
      if(deal_ticket > 0 && HistoryDealSelect(deal_ticket))
         pos_id = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);

      if(pos_id == 0)
        {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
           {
            ulong t = PositionGetTicket(i);
            if(t > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == (long)g_auto_magic)
              {
               pos_id = t;
               break;
              }
           }
        }

      if(pos_id > 0)
        {
         SetTicketState("RISK", pos_id, sl_dist);
         SetTicketState("RISKUSD", pos_id, trade_risk_usd);
         SetTicketState("TP1", pos_id, 0.0);
         SetTicketState("BE", pos_id, 0.0);
         SetTicketState("MFE", pos_id, 0.0);
         SetTicketState("MAE", pos_id, 0.0);
         ENUM_AUTO_STRATEGY opened_strategy=STRAT_BREAKOUT;
         if(StringFind(engine_name,"Pullback")>=0) opened_strategy=STRAT_PULLBACK;
         else if(StringFind(engine_name,"Sweep")>=0) opened_strategy=STRAT_SWEEP;
         else if(StringFind(engine_name,"Momentum")>=0) opened_strategy=STRAT_MOMENTUM;
         SetTicketState("STRAT",pos_id,(double)opened_strategy);
         double fill_price = g_trade.ResultPrice();
         double fill_slippage_points = (order_type == ORDER_TYPE_BUY ? fill_price - ask : bid - fill_price) / point;
         NotifyEvent("ORDER_OPENED", StringFormat("#%I64u | %s | %.4f lots | execute %.5f | slippage %.1f pts | SL %.5f | risk %.2f$", pos_id, engine_name, lots, fill_price, fill_slippage_points, sl, trade_risk_usd));
         if(IsSymbolCrypto(_Symbol)) RegisterCryptoTradeOpened();
        }
      else
         NotifyEvent("ORDER_OPENED_NO_POSITION_ID", StringFormat("%s | deal #%I64u", engine_name, deal_ticket));
     }
   else
     {
      RegisterMarketClosedBackoff("ENTRY_"+engine_name,0);
      NotifyEvent("ORDER_REJECTED", StringFormat("%s | code %u | %s", engine_name, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription()));
     }

   ReleaseGlobalTradeLock();
  }

string MarketClassToString(ENUM_MARKET_CLASS cls)
  {
   switch(cls)
     {
      case MARKET_CRYPTO: return "CRYPTO";
      case MARKET_FOREX:  return "FOREX";
      case MARKET_GOLD:   return "GOLD";
      case MARKET_OIL:    return "OIL";
      case MARKET_INDEX:  return "INDEX";
      default:            return "OTHER";
     }
  }

ENUM_MARKET_CLASS DetectMarketClass(string symbol)
  {
   string u=symbol; StringToUpper(u);
   if(IsSymbolCrypto(u)) return MARKET_CRYPTO;
   if(StringFind(u,"XAU")>=0 || StringFind(u,"GOLD")>=0) return MARKET_GOLD;
   if(StringFind(u,"WTI")>=0 || StringFind(u,"USOIL")>=0 || StringFind(u,"XTI")>=0 || StringFind(u,"UKOIL")>=0 || StringFind(u,"BRENT")>=0) return MARKET_OIL;
   if(StringFind(u,"US30")>=0 || StringFind(u,"DJI")>=0 || StringFind(u,"NAS")>=0 || StringFind(u,"USTEC")>=0 || StringFind(u,"SPX")>=0 || StringFind(u,"US500")>=0 || StringFind(u,"GER")>=0 || StringFind(u,"DE40")>=0 || StringFind(u,"FRA40")>=0 || StringFind(u,"UK100")>=0) return MARKET_INDEX;
   string b,q; if(GetSymbolCurrencies(u,b,q)) return MARKET_FOREX;
   return MARKET_OTHER;
  }

string AssetProfileToString(ENUM_ASSET_PROFILE p)
  {
   switch(p)
     {
      case PROFILE_CRYPTO_LIQUID: return "CRYPTO_LIQUID";
      case PROFILE_CRYPTO_MAJOR: return "CRYPTO_MAJOR";
      case PROFILE_CRYPTO_LIQUID_ALT: return "CRYPTO_LIQUID_ALT";
      case PROFILE_CRYPTO_DISTINCT: return "CRYPTO_DISTINCT";
      case PROFILE_CRYPTO_HIGH_BETA: return "CRYPTO_HIGH_BETA";
      case PROFILE_CRYPTO_OTHER:  return "CRYPTO_OTHER";
      case PROFILE_FOREX:         return "FOREX";
      case PROFILE_GOLD:          return "GOLD";
      case PROFILE_OIL:           return "OIL";
      case PROFILE_INDEX:         return "INDEX";
      default:                    return "OTHER";
     }
  }

bool IsCryptoMajorSymbol(string symbol)
  {
   string u=symbol; StringToUpper(u);
   return (StringFind(u,"BTC")>=0 || StringFind(u,"ETH")>=0);
  }

bool IsCryptoDistinctSymbol(string symbol)
  {
   string u=symbol; StringToUpper(u);
   // Actifs historiquement distincts dans les données HF étudiées; la liste reste volontairement courte.
   return (StringFind(u,"DASH")>=0 || StringFind(u,"XMR")>=0 || StringFind(u,"ETC")>=0);
  }

bool IsCryptoLiquidAltSymbol(string symbol)
  {
   string u=symbol; StringToUpper(u);
   string liquid_alt[] = {"ADA","XRP","LTC","BCH","SOL","LINK","DOT","AVAX","DOGE","BNB","TRX","XLM"};
   for(int i=0;i<ArraySize(liquid_alt);i++) if(StringFind(u,liquid_alt[i])>=0) return true;
   return false;
  }

enum ENUM_CRYPTO_REGIME { CRYPTO_REGIME_NORMAL=0, CRYPTO_REGIME_PRE_SHOCK, CRYPTO_REGIME_SHOCK, CRYPTO_REGIME_POST_SHOCK };

static datetime g_crypto_last_shock_bar=0;

ENUM_CRYPTO_REGIME DetectCryptoRegime(double atr_ratio,double candle_range_atr)
  {
   datetime bar=iTime(_Symbol,InpCryptoSetupTF,1);
   if(atr_ratio>=InpCryptoJumpATRRatio || candle_range_atr>=InpCryptoJumpCandleATR)
     { g_crypto_last_shock_bar=bar; return CRYPTO_REGIME_SHOCK; }
   if(g_crypto_last_shock_bar>0)
     {
      int shift=iBarShift(_Symbol,InpCryptoSetupTF,g_crypto_last_shock_bar,true);
      if(shift>=0 && shift<=InpCryptoPostShockBars) return CRYPTO_REGIME_POST_SHOCK;
     }
   if(atr_ratio>=InpCryptoPreShockATRRatio || candle_range_atr>=InpCryptoPreShockCandleATR) return CRYPTO_REGIME_PRE_SHOCK;
   return CRYPTO_REGIME_NORMAL;
  }

string CryptoRegimeToString(ENUM_CRYPTO_REGIME r)
  {
   if(r==CRYPTO_REGIME_PRE_SHOCK) return "PRE_SHOCK";
   if(r==CRYPTO_REGIME_SHOCK) return "SHOCK";
   if(r==CRYPTO_REGIME_POST_SHOCK) return "POST_SHOCK";
   return "NORMAL";
  }

bool GetBTCContext(bool &bullish,bool &bearish,double &strength,string &reason)
  {
   bullish=false; bearish=false; strength=0.0; reason="BTC context unavailable";
   if(g_market_class!=MARKET_CRYPTO) return true;
   string btc=_Symbol; StringToUpper(btc);
   if(StringFind(btc,"BTC")>=0)
     {
      ENUM_TIMEFRAMES tf=InpCryptoSetupTF;
      double c1=iClose(_Symbol,tf,1), c2=iClose(_Symbol,tf,2);
      int h=iMA(_Symbol,tf,InpCryptoBTCContextEMA,0,MODE_EMA,PRICE_CLOSE);
      if(h==INVALID_HANDLE) return false;
      double e[1];
      bool ok=(CopyBuffer(h,0,1,1,e)>=1);
      IndicatorRelease(h);
      if(!ok || c1<=0 || c2<=0) return false;
      bullish=(c1>e[0] && c1>c2); bearish=(c1<e[0] && c1<c2);
      strength=(bullish||bearish)?1.0:0.0;
      reason=bullish?"BTC bullish context":(bearish?"BTC bearish context":"BTC neutral context");
      return true;
     }
   // Pour les altcoins, BTCUSD doit exister chez le broker; sinon on ne fabrique pas un contexte.
   string candidates[] = {"BTCUSD_BT","BTCUSD","BTCUSD.a","BTCUSDm","BTCUSDT","BTCUSDT.a"};
   string bs="";
   for(int i=0;i<ArraySize(candidates);i++) if(SymbolSelect(candidates[i],true)) { bs=candidates[i]; break; }
   if(bs=="") { reason="BTC symbol unavailable"; return false; }
   ENUM_TIMEFRAMES tf=InpCryptoSetupTF;
   double c1=iClose(bs,tf,1), c2=iClose(bs,tf,2);
   int h=iMA(bs,tf,InpCryptoBTCContextEMA,0,MODE_EMA,PRICE_CLOSE);
   if(h==INVALID_HANDLE) return false;
   double e[1];
   bool ok=(CopyBuffer(h,0,1,1,e)>=1);
   IndicatorRelease(h);
   if(!ok || c1<=0 || c2<=0) return false;
   bullish=(c1>e[0] && c1>c2); bearish=(c1<e[0] && c1<c2);
   strength=(bullish||bearish)?1.0:0.0;
   reason=bullish?"BTC bullish context":(bearish?"BTC bearish context":"BTC neutral context");
   return true;
  }

bool CryptoBTCAlignedForTrade(ENUM_ASSET_PROFILE p,ENUM_ORDER_TYPE ot,bool btc_bull,bool btc_bear,double btc_strength=1.0)
  {
   // Majors: pas de filtre BTC externe. Alts: neutre autorisé si force < seuil d'alignement.
   if(g_market_class!=MARKET_CRYPTO || IsCryptoMajorSymbol(_Symbol)) return true;
   if(btc_strength < InpCryptoAltBTCMinAlign)
      return true; // contexte neutre / faible → on n'impose pas la direction BTC
   if(p==PROFILE_CRYPTO_LIQUID || p==PROFILE_CRYPTO_LIQUID_ALT || p==PROFILE_CRYPTO_DISTINCT || p==PROFILE_CRYPTO_HIGH_BETA)
     {
      if(ot==ORDER_TYPE_BUY) return btc_bull;
      return btc_bear;
     }
   return true;
  }

bool CryptoProfileAllowsStrategy(ENUM_AUTO_STRATEGY st,ENUM_ASSET_PROFILE p,ENUM_MARKET_REGIME r,ENUM_CRYPTO_REGIME cr)
  {
   bool trend=(r==REGIME_TREND || r==REGIME_HIGH_VOL_TREND);
   bool range=(r==REGIME_RANGE || r==REGIME_HIGH_VOL_RANGE);
   if(cr==CRYPTO_REGIME_SHOCK)
     {
      // Choc : aucune continuation. Seul le sweep/reversal est admissible.
      return (st==STRAT_SWEEP);
     }
   if(cr==CRYPTO_REGIME_PRE_SHOCK || cr==CRYPTO_REGIME_POST_SHOCK)
     {
      // Zone de transition commune à toutes les crypto : pas de poursuite agressive.
      if(st==STRAT_SWEEP) return (cr==CRYPTO_REGIME_PRE_SHOCK ? range : true);
      if(st==STRAT_PULLBACK) return (cr==CRYPTO_REGIME_POST_SHOCK ? trend : false);
      return false;
     }
   if(p==PROFILE_CRYPTO_MAJOR)
     {
      if(st==STRAT_SWEEP) return range;
      if(st==STRAT_MOMENTUM) return trend;
      if(st==STRAT_BREAKOUT || st==STRAT_PULLBACK) return trend;
     }
   if(p==PROFILE_CRYPTO_LIQUID_ALT)
     {
      if(st==STRAT_SWEEP) return range;
      if(st==STRAT_MOMENTUM) return trend;
      return trend;
     }
   if(p==PROFILE_CRYPTO_DISTINCT)
     {
      if(st==STRAT_SWEEP) return range;
      if(st==STRAT_MOMENTUM) return trend;
      if(st==STRAT_PULLBACK) return trend;
      return trend;
     }
   if(p==PROFILE_CRYPTO_HIGH_BETA)
     {
      if(st==STRAT_SWEEP) return range;
      if(st==STRAT_PULLBACK) return trend;
      if(st==STRAT_MOMENTUM) return (r==REGIME_TREND);
      return false;
     }
   return (st==STRAT_SWEEP ? range : trend);
  }

bool IsCryptoLiquidProfile(ENUM_TIMEFRAMES tf,double atr_value)
  {
   if(g_market_class!=MARKET_CRYPTO || atr_value<=0.0) return false;
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK), bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double spread=ask-bid;
   double relvol=0.0;
   double v1=(double)iVolume(_Symbol,tf,1);
   double sum=0.0; int n=0;
   for(int i=2;i<32;i++) { double v=(double)iVolume(_Symbol,tf,i); if(v>0.0){sum+=v;n++;} }
   if(n>0) relvol=v1/(sum/n);
   return ((spread/atr_value)<=InpCryptoLiquidMaxSpreadATR && relvol>=InpCryptoLiquidMinRelVolume);
  }

ENUM_ASSET_PROFILE GetAssetProfile(ENUM_TIMEFRAMES tf,double atr_value)
  {
   if(g_market_class==MARKET_CRYPTO && InpCryptoRegimeEngine)
     {
      if(IsCryptoMajorSymbol(_Symbol)) return PROFILE_CRYPTO_MAJOR;
      if(IsCryptoDistinctSymbol(_Symbol)) return PROFILE_CRYPTO_DISTINCT;
      if(IsCryptoLiquidAltSymbol(_Symbol) && IsCryptoLiquidProfile(tf,atr_value)) return PROFILE_CRYPTO_LIQUID_ALT;
      if(IsCryptoLiquidProfile(tf,atr_value)) return PROFILE_CRYPTO_LIQUID;
      // Faible liquidité / forte volatilité: profil défensif.
      return PROFILE_CRYPTO_HIGH_BETA;
     }
   if(!InpEnableMarketProfile)
     {
      if(g_market_class==MARKET_CRYPTO) return PROFILE_CRYPTO_OTHER;
      if(g_market_class==MARKET_FOREX) return PROFILE_FOREX;
      if(g_market_class==MARKET_GOLD) return PROFILE_GOLD;
      if(g_market_class==MARKET_OIL) return PROFILE_OIL;
      if(g_market_class==MARKET_INDEX) return PROFILE_INDEX;
      return PROFILE_OTHER;
     }
   if(g_market_class==MARKET_CRYPTO) return (IsCryptoLiquidProfile(tf,atr_value) ? PROFILE_CRYPTO_LIQUID : PROFILE_CRYPTO_OTHER);
   if(g_market_class==MARKET_FOREX) return PROFILE_FOREX;
   if(g_market_class==MARKET_GOLD) return PROFILE_GOLD;
   if(g_market_class==MARKET_OIL) return PROFILE_OIL;
   if(g_market_class==MARKET_INDEX) return PROFILE_INDEX;
   return PROFILE_OTHER;
  }

bool StrategyAllowedForProfile(ENUM_AUTO_STRATEGY st,ENUM_ASSET_PROFILE p,ENUM_MARKET_REGIME r)
  {
   // PROD D017 v11.16 : Momentum uniquement. Breakout/Pullback/Sweep restent dans le source
   // pour audit historique, mais ne peuvent generer aucun nouveau signal de production.
   if(st!=STRAT_MOMENTUM) return false;
   if(st==STRAT_BREAKOUT && !InpAllowBreakout) return false;
   if(st==STRAT_PULLBACK && !InpAllowPullback) return false;
   if(st==STRAT_SWEEP && !InpAllowSweep) return false;
   if(st==STRAT_MOMENTUM && !InpAllowMomentum) return false;
   bool trend=(r==REGIME_TREND || r==REGIME_HIGH_VOL_TREND);
   bool range=(r==REGIME_RANGE);
   bool high_range=(r==REGIME_HIGH_VOL_RANGE);
   if(g_market_class==MARKET_CRYPTO)
     {
      // Fallback: régime marché global seulement (CheckSignals passe le vrai crypto_regime).
      return CryptoProfileAllowsStrategy(st,p,r,CRYPTO_REGIME_NORMAL);
     }
   if(p==PROFILE_FOREX)
     {
      if(st==STRAT_SWEEP) return (range || high_range);
      return trend;
     }
   if(p==PROFILE_GOLD)
     {
      if(st==STRAT_SWEEP) return (range || high_range);
      return trend;
     }
   if(p==PROFILE_OIL)
     {
      if(st==STRAT_SWEEP) return range;
      return trend;
     }
   if(p==PROFILE_INDEX)
     {
      if(st==STRAT_SWEEP) return range;
      return trend;
     }
   if(st==STRAT_SWEEP) return false;
   return trend;
  }

double GetStructuralSLDistance(double atr_value,double entry_price,double structural_level,ENUM_ORDER_TYPE ot,ENUM_AUTO_STRATEGY st,ENUM_ASSET_PROFILE p)
  {
   if(atr_value<=0.0 || entry_price<=0.0) return 0.0;
   double structural=(ot==ORDER_TYPE_BUY ? entry_price-structural_level : structural_level-entry_price);
   if(structural<=0.0) structural=atr_value*0.50;
   double buffer=atr_value*InpStructuralSLBufferATR;
   double floor_dist=atr_value*(st==STRAT_SWEEP ? 0.50 : (st==STRAT_PULLBACK ? 0.65 : 0.75));
   if(g_market_class!=MARKET_CRYPTO)
      floor_dist=MathMax(floor_dist,atr_value*InpSL_ATR_Multiplier);
   // Crypto: plancher anti-bruit (évite SL à 60$ sur BTC) + plafond pour garder un lot viable sous risk %
   if(g_market_class == MARKET_CRYPTO || p == PROFILE_CRYPTO_MAJOR || p == PROFILE_CRYPTO_LIQUID ||
      p == PROFILE_CRYPTO_LIQUID_ALT || p == PROFILE_CRYPTO_HIGH_BETA || p == PROFILE_CRYPTO_OTHER ||
      p == PROFILE_CRYPTO_DISTINCT)
     {
      floor_dist = MathMax(floor_dist, atr_value * InpCryptoSLFloorATR);
     }
   double d=structural+buffer;
   if(d<floor_dist) d=floor_dist;
   if(g_market_class == MARKET_CRYPTO && InpCryptoSLCapATR > 0.0)
     {
      double cap = atr_value * InpCryptoSLCapATR;
      if(d > cap) d = cap;
     }
   return d;
  }

double GetSweepSLDistance(double atr_value,double sweep_extreme,double entry_price,ENUM_ORDER_TYPE ot,ENUM_ASSET_PROFILE p)
  {
   return GetStructuralSLDistance(atr_value,entry_price,sweep_extreme,ot,STRAT_SWEEP,p);
  }

ENUM_TIMEFRAMES GetProfileSetupTF(string engine)
  {
   if(!InpAutoSetupTimeframe) return _Period;
   if(g_market_class==MARKET_CRYPTO) return InpCryptoSetupTF;
   return InpClassicSetupTF;
  }

ENUM_TIMEFRAMES GetProfileMacroTF()
  {
   if(!InpAutoSetupTimeframe) return InpMacroTrendTF;
   if(g_market_class==MARKET_CRYPTO) return InpCryptoMacroTF;
   return InpClassicMacroTF;
  }

// Confirmation directionnelle crypto : la macro H1 reste le filtre de fond,
// mais le Momentum crypto doit aussi être confirmé par la structure du TF setup
// et une EMA courte. Cette règle ne s'applique PAS au Forex.
bool CryptoDirectionConfirmed(ENUM_TIMEFRAMES setup_tf,ENUM_ORDER_TYPE order_type,string &reason)
  {
   reason="";
   if(!InpCryptoDirectionFilter || g_market_class!=MARKET_CRYPTO) return true;
   if(InpCryptoDirectionEMA<2) return false;

   int ema_handle=iMA(_Symbol,setup_tf,InpCryptoDirectionEMA,0,MODE_EMA,PRICE_CLOSE);
   if(ema_handle==INVALID_HANDLE)
     {
      reason="EMA direction indisponible";
      return false;
     }

   double ema1[1],ema3[1];
   if(CopyBuffer(ema_handle,0,1,1,ema1)<1 || CopyBuffer(ema_handle,0,3,1,ema3)<1)
     {
      IndicatorRelease(ema_handle);
      reason="EMA direction non disponible";
      return false;
     }
   IndicatorRelease(ema_handle);

   double close1=iClose(_Symbol,setup_tf,1);
   double close2=iClose(_Symbol,setup_tf,2);
   double low1=iLow(_Symbol,setup_tf,1);
   double low3=iLow(_Symbol,setup_tf,3);
   double high1=iHigh(_Symbol,setup_tf,1);
   double high3=iHigh(_Symbol,setup_tf,3);

   if(order_type==ORDER_TYPE_BUY)
     {
      bool ema_ok=(close1>ema1[0] && ema1[0]>ema3[0]);
      bool structure_ok=(close1>close2 && low1>=low3);
      if(ema_ok && structure_ok)
        { reason="Crypto direction OK | EMA + structure haussiere"; return true; }
      reason=StringFormat("Crypto direction BLOCK | EMA=%s | structure=%s",ema_ok?"OK":"NO",structure_ok?"OK":"NO");
      return false;
     }

   bool ema_ok=(close1<ema1[0] && ema1[0]<ema3[0]);
   bool structure_ok=(close1<close2 && high1<=high3);
   if(ema_ok && structure_ok)
     { reason="Crypto direction OK | EMA + structure baissiere"; return true; }
   reason=StringFormat("Crypto direction BLOCK | EMA=%s | structure=%s",ema_ok?"OK":"NO",structure_ok?"OK":"NO");
   return false;
  }

bool GetRelativeATRFromHandle(int atr_handle,double &ratio)
  {
   ratio=1.0;
   if(atr_handle==INVALID_HANDLE) return false;
   double cur[]; ArraySetAsSeries(cur,true);
   if(CopyBuffer(atr_handle,0,1,1,cur)<1 || cur[0]<=0.0) return false;
   double hist[]; ArraySetAsSeries(hist,true);
   int need=30;
   if(CopyBuffer(atr_handle,0,2,need,hist)<need) return false;
   double sum=0.0; int n=0;
   for(int i=0;i<need;i++) if(hist[i]>0.0) { sum+=hist[i]; n++; }
   if(n==0) return false;
   ratio=cur[0]/(sum/n);
   return true;
  }

ENUM_MARKET_REGIME DetectMarketRegime(double adx,double atr_ratio)
  {
   if(adx<=0.0) return REGIME_UNKNOWN;
   bool trend=(adx>=InpADX_TrendThreshold);
   bool high=(atr_ratio>=1.25);
   bool low=(atr_ratio<=0.80);
   if(trend && high) return REGIME_HIGH_VOL_TREND;
   if(!trend && high) return REGIME_HIGH_VOL_RANGE;
   if(trend) return REGIME_TREND;
   if(low) return REGIME_LOW_VOL;
   return REGIME_RANGE;
  }

string MarketRegimeToString(ENUM_MARKET_REGIME r)
  {
   switch(r)
     {
      case REGIME_TREND: return "TREND";
      case REGIME_RANGE: return "RANGE";
      case REGIME_HIGH_VOL_TREND: return "HIGH_VOL_TREND";
      case REGIME_HIGH_VOL_RANGE: return "HIGH_VOL_RANGE";
      case REGIME_LOW_VOL: return "LOW_VOL";
      default: return "UNKNOWN";
     }
  }

enum ENUM_SESSION_PHASE
  {
   SESSION_ASIA=0,
   SESSION_LONDON,
   SESSION_OVERLAP,
   SESSION_NEWYORK,
   SESSION_OTHER,
   SESSION_WEEKEND
  };

ENUM_SESSION_PHASE GetSessionPhase(string symbol, datetime t)
  {
   MqlDateTime dt; TimeToStruct(t,dt);
   if(LedgerAssetClass(symbol)=="CRYPTO" && (dt.day_of_week==0 || dt.day_of_week==6)) return SESSION_WEEKEND;
   double h=dt.hour+dt.min/60.0;
   if(h>=8.0 && h<13.5) return SESSION_LONDON;
   if(h>=13.5 && h<16.5) return SESSION_OVERLAP;
   if(h>=16.5 && h<21.0) return SESSION_NEWYORK;
   if(h<8.0) return SESSION_ASIA;
   return SESSION_OTHER;
  }

string SessionPhaseToString(ENUM_SESSION_PHASE p)
  {
   switch(p)
     {
      case SESSION_ASIA: return "ASIA";
      case SESSION_LONDON: return "LONDON";
      case SESSION_OVERLAP: return "LONDON_NY";
      case SESSION_NEWYORK: return "NEW_YORK";
      case SESSION_WEEKEND: return "WEEKEND";
      default: return "OTHER";
     }
  }

ENUM_SIGNAL_GRADE GradeFromScore(double score)
  {
   if(score>=InpScoreAPlus) return SIGNAL_A_PLUS;
   if(score>=InpScoreA) return SIGNAL_A;
   if(score>=InpScoreB) return SIGNAL_B;
   return SIGNAL_C;
  }

string GradeToString(ENUM_SIGNAL_GRADE g)
  {
   switch(g)
     {
      case SIGNAL_A_PLUS: return "A+";
      case SIGNAL_A: return "A";
      case SIGNAL_B: return "B";
      default: return "C";
     }
  }

double GradeRiskFactor(ENUM_SIGNAL_GRADE g)
  {
   switch(g)
     {
      case SIGNAL_A_PLUS: return InpQualityRiskAPlus;
      case SIGNAL_A: return InpQualityRiskA;
      case SIGNAL_B: return InpQualityRiskB;
      default: return InpQualityRiskC;
     }
  }

double ScoreSignalContext(string engine,bool macro_bullish,bool macro_bearish,bool is_trending,double spread,double sl_dist,datetime now)
  {
   double score=50.0;
   bool buy=(StringFind(engine,"BUY")>=0);
   bool sell=(StringFind(engine,"SELL")>=0);
   bool breakout=(StringFind(engine,"Breakout")>=0);
   bool pullback=(StringFind(engine,"Pullback")>=0);
   bool sweep=(StringFind(engine,"Sweep")>=0);
   bool momentum=(StringFind(engine,"Momentum")>=0);
   if((buy && macro_bullish)||(sell && macro_bearish)) score+=15.0;
   if((breakout||pullback||momentum) && is_trending) score+=10.0;
   if(sweep && !is_trending) score+=10.0;
   if(g_market_regime==REGIME_HIGH_VOL_TREND && (breakout||momentum)) score+=8.0;
   if(g_market_regime==REGIME_TREND && (breakout||pullback||momentum)) score+=5.0;
   if(g_market_regime==REGIME_RANGE && sweep) score+=10.0;
   if(g_market_regime==REGIME_HIGH_VOL_RANGE && sweep) score-=5.0;
   if(g_market_regime==REGIME_LOW_VOL) score-=8.0;
   if(g_relative_atr_ratio>=0.85 && g_relative_atr_ratio<=1.75) score+=5.0;
   if(g_relative_atr_ratio>1.75 && !breakout && !momentum) score-=5.0;
   ENUM_SESSION_PHASE sp=GetSessionPhase(_Symbol,now);
   if(g_market_class==MARKET_CRYPTO)
     {
      if(sp==SESSION_OVERLAP || sp==SESSION_NEWYORK) score+=5.0;
     }
   else if(sp==SESSION_LONDON || sp==SESSION_OVERLAP || sp==SESSION_NEWYORK) score+=4.0;
   if(sl_dist>0.0)
     {
      double pct=(spread/sl_dist)*100.0;
      if(pct<=5.0) score+=3.0;
      else if(pct>10.0) score-=5.0;
     }
   if(g_market_class==MARKET_OIL && sweep) score-=8.0;
   if(g_market_class==MARKET_INDEX && sweep && g_market_regime==REGIME_HIGH_VOL_RANGE) score-=4.0;
   if(g_market_class==MARKET_CRYPTO)
     {
      if(g_market_profile_name=="CRYPTO_HIGH_BETA" && (breakout||momentum)) score-=8.0;
      if(g_market_profile_name=="CRYPTO_DISTINCT" && sweep) score+=3.0;
      if(g_market_profile_name=="CRYPTO_MAJOR" && momentum && g_market_regime==REGIME_HIGH_VOL_TREND) score+=3.0;
     }
   if(score<0.0) score=0.0; if(score>100.0) score=100.0;
   return score;
  }

void AddCandidate(SignalCandidate &cands[],ENUM_ORDER_TYPE ot,string engine,string reason,double score,double sl_dist)
  {
   int n=ArraySize(cands); ArrayResize(cands,n+1);
   cands[n].valid=true; cands[n].order_type=ot; cands[n].engine=engine; cands[n].reason=reason;
   cands[n].score=score; cands[n].grade=GradeFromScore(score); cands[n].risk_factor=GradeRiskFactor(cands[n].grade); cands[n].sl_dist=sl_dist;
  }

int BestCandidateIndex(SignalCandidate &cands[])
  {
   int best=-1;
   for(int i=0;i<ArraySize(cands);i++)
     {
      if(!cands[i].valid || cands[i].risk_factor<=0.0) continue;
      if(best<0 || cands[i].score>cands[best].score) best=i;
     }
   return best;
  }

void UpdateMarketProfile()
  {
   g_market_class=DetectMarketClass(_Symbol);
   g_market_profile_name=MarketClassToString(g_market_class);
   double ar=1.0; if(GetRelativeATRFromHandle(g_atr_handle,ar)) g_relative_atr_ratio=ar;
   double adx_arr[]; ArraySetAsSeries(adx_arr,true);
   if(CopyBuffer(g_adx_handle,0,1,1,adx_arr)>0) g_market_regime=DetectMarketRegime(adx_arr[0],g_relative_atr_ratio);
  }


bool ModeAllowsStrategy(ENUM_AUTO_STRATEGY st)
  {
   if(InpStrategyMode==MODE_MANUAL_GUARDIAN_ONLY) return false;
   if(InpStrategyMode==MODE_BREAKOUT_ONLY) return (st==STRAT_BREAKOUT);
   if(InpStrategyMode==MODE_TREND_ONLY) return (st==STRAT_BREAKOUT || st==STRAT_PULLBACK);
   return true; // ADAPTIVE_REGIME / PORTFOLIO_RANKED
  }

double ProfileMinATRPercent(ENUM_ASSET_PROFILE p)
  {
   if(p==PROFILE_CRYPTO_LIQUID || p==PROFILE_CRYPTO_MAJOR || p==PROFILE_CRYPTO_LIQUID_ALT || p==PROFILE_CRYPTO_DISTINCT || p==PROFILE_CRYPTO_HIGH_BETA || p==PROFILE_CRYPTO_OTHER) return MathMin(InpMinATRPercent,0.03);
   return InpMinATRPercent;
  }

double ProfileMaxATRPercent(ENUM_ASSET_PROFILE p)
  {
   if(p==PROFILE_CRYPTO_LIQUID || p==PROFILE_CRYPTO_MAJOR || p==PROFILE_CRYPTO_LIQUID_ALT || p==PROFILE_CRYPTO_DISTINCT || p==PROFILE_CRYPTO_HIGH_BETA || p==PROFILE_CRYPTO_OTHER) return MathMax(InpMaxATRPercent,3.00);
   if(p==PROFILE_GOLD || p==PROFILE_OIL || p==PROFILE_INDEX) return MathMax(InpMaxATRPercent,1.50);
   return InpMaxATRPercent;
  }

void CheckSignals()
  {
   if(InpStrategyMode==MODE_MANUAL_GUARDIAN_ONLY) return;
   UpdateMarketProfile();
   ENUM_TIMEFRAMES setup_tf=GetProfileSetupTF("PORTFOLIO");
   ENUM_TIMEFRAMES macro_tf=GetProfileMacroTF();

   int macro_handle=g_macro_ema_handle;
   if(macro_handle==INVALID_HANDLE) return;
   // Lecture explicite des shifts pour eviter toute ambiguite d'indexation CopyBuffer.
   double macro_ema_closed_arr[1], macro_ema_older_arr[1];
   if(CopyBuffer(macro_handle,0,1,1,macro_ema_closed_arr)<1 ||
      CopyBuffer(macro_handle,0,4,1,macro_ema_older_arr)<1)
     { return; }
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double close_cur=iClose(_Symbol,setup_tf,1);
   if(close_cur<=0.0) return;
   double macro_ema_closed=macro_ema_closed_arr[0];
   double macro_ema_older=macro_ema_older_arr[0];
   double ema_slope=macro_ema_closed-macro_ema_older;
   double macro_atr=0.0;
   int macro_atr_handle=iATR(_Symbol,macro_tf,InpATR_Period);
   if(macro_atr_handle!=INVALID_HANDLE)
     { double ma[1]; if(CopyBuffer(macro_atr_handle,0,1,1,ma)>=1) macro_atr=ma[0]; IndicatorRelease(macro_atr_handle); }
   double slope_norm=(macro_atr>0.0 ? ema_slope/macro_atr : 0.0);
   bool macro_bullish,macro_bearish;
   if(g_market_class==MARKET_CRYPTO && InpCryptoRegimeEngine)
     {
      const double crypto_slope_threshold=0.05;
      macro_bullish=(close_cur>macro_ema_closed) && slope_norm>crypto_slope_threshold;
      macro_bearish=(close_cur<macro_ema_closed) && slope_norm<-crypto_slope_threshold;
      if(InpEnableCryptoDebugLogs)
         NotifyEvent("CRYPTO_MACRO_CALIBRATION",StringFormat("%s | slope %.8f | ATRmacro %.8f | slope/ATR %.4f | min %.4f | bull %s | bear %s",_Symbol,ema_slope,macro_atr,slope_norm,crypto_slope_threshold,(macro_bullish?"YES":"NO"),(macro_bearish?"YES":"NO")));
     }
   else
     {
      macro_bullish=(close_cur>macro_ema_closed) && ema_slope>3*point;
      macro_bearish=(close_cur<macro_ema_closed) && ema_slope<-3*point;
     }

   int adx_handle=g_adx_handle;
   int atr_handle_tmp=g_atr_handle;
   if(adx_handle==INVALID_HANDLE || atr_handle_tmp==INVALID_HANDLE) return;
   double adx_val[],atr_setup[]; ArraySetAsSeries(adx_val,true); ArraySetAsSeries(atr_setup,true);
   if(CopyBuffer(adx_handle,0,1,1,adx_val)<=0 || CopyBuffer(atr_handle_tmp,0,1,1,atr_setup)<=0 || atr_setup[0]<=0.0) return;
   bool is_trending=(adx_val[0]>=InpADX_TrendThreshold);
   ENUM_MARKET_REGIME regime=(adx_val[0]>=InpADX_TrendThreshold ? REGIME_TREND : REGIME_RANGE);
   double ar=1.0; if(GetRelativeATRFromHandle(atr_handle_tmp,ar)) g_relative_atr_ratio=ar;
   g_market_regime=DetectMarketRegime(adx_val[0],g_relative_atr_ratio);
   ENUM_ASSET_PROFILE profile=GetAssetProfile(setup_tf,atr_setup[0]);
   string profile_name=AssetProfileToString(profile); g_market_profile_name=profile_name;

   int donchian_n = (g_market_class==MARKET_CRYPTO ? InpDonchianPeriodCrypto : InpDonchianPeriod);
   if(donchian_n < 5) donchian_n = 5;
   int hi_bar=iHighest(_Symbol,setup_tf,MODE_HIGH,donchian_n,2);
   int lo_bar=iLowest(_Symbol,setup_tf,MODE_LOW,donchian_n,2);
   if(hi_bar<0 || lo_bar<0) return;
   double donchian_high=iHigh(_Symbol,setup_tf,hi_bar), donchian_low=iLow(_Symbol,setup_tf,lo_bar);
   double high1=iHigh(_Symbol,setup_tf,1), low1=iLow(_Symbol,setup_tf,1), open1=iOpen(_Symbol,setup_tf,1), close1=iClose(_Symbol,setup_tf,1);
   double high2=iHigh(_Symbol,setup_tf,2), low2=iLow(_Symbol,setup_tf,2), open2=iOpen(_Symbol,setup_tf,2), close2=iClose(_Symbol,setup_tf,2);
   ENUM_CRYPTO_REGIME crypto_regime=CRYPTO_REGIME_NORMAL;
   bool btc_bull=false, btc_bear=false; double btc_strength=0.0; string btc_reason="";
   if(g_market_class==MARKET_CRYPTO)
     {
      double candle_range_atr=(atr_setup[0]>0.0 ? (high1-low1)/atr_setup[0] : 0.0);
      crypto_regime=DetectCryptoRegime(g_relative_atr_ratio,candle_range_atr);
      GetBTCContext(btc_bull,btc_bear,btc_strength,btc_reason);
      if(InpEnableCryptoDebugLogs)
         NotifyEvent("CRYPTO_REGIME_STATE",StringFormat("%s | %s | ATRrel %.2f | candle %.2f ATR | profile %s",_Symbol,CryptoRegimeToString(crypto_regime),g_relative_atr_ratio,candle_range_atr,profile_name));
     }
   bool crypto_extended=false;
   if(g_market_class==MARKET_CRYPTO && InpCryptoRegimeEngine)
     {
      double setup_ema=0.0; int eh=iMA(_Symbol,setup_tf,InpCryptoDirectionEMA,0,MODE_EMA,PRICE_CLOSE);
      if(eh!=INVALID_HANDLE) { double ev[1]; if(CopyBuffer(eh,0,1,1,ev)>=1 && atr_setup[0]>0.0) { setup_ema=ev[0]; crypto_extended=(MathAbs(close_cur-setup_ema)/atr_setup[0]>=InpCryptoExtensionATR); } IndicatorRelease(eh); }
      if(crypto_extended && InpEnableCryptoDebugLogs) NotifyEvent("CRYPTO_EXTENSION_BLOCK",StringFormat("%s | distance EMA/ATR %.2f | threshold %.2f",_Symbol,(atr_setup[0]>0.0?MathAbs(close_cur-setup_ema)/atr_setup[0]:0.0),InpCryptoExtensionATR));
     }
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
   
   if(InpEnableQualityFilter)
     {
      double atr_percent=(close_cur>0.0 ? atr_setup[0]/close_cur*100.0 : 0.0);
      if(atr_percent<ProfileMinATRPercent(profile) || atr_percent>ProfileMaxATRPercent(profile)) return;
      if((high1-low1)>atr_setup[0]*InpMaxSignalCandleATR) return;
     }

   SignalCandidate cands[]; datetime now=TimeCurrent();
   bool volume_ok=IsRelativeTickVolumePresent();

   double entry_probe_buy=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double entry_probe_sell=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double sl_breakout_buy=GetStructuralSLDistance(atr_setup[0],entry_probe_buy,donchian_high,ORDER_TYPE_BUY,STRAT_BREAKOUT,profile);
   double sl_breakout_sell=GetStructuralSLDistance(atr_setup[0],entry_probe_sell,donchian_low,ORDER_TYPE_SELL,STRAT_BREAKOUT,profile);

   //--- Pullback structurel : impulsion identifiable puis retracement des barres 2/1.
   //    L'impulsion est ancrée sur la barre 3 (juste avant le retracement).
   //    Le swing SL reste limité aux barres de retracement 2/1.
   double pb_impulse_low=DBL_MAX, pb_impulse_high=0.0;
   int pb_lookback=MathMax(3,InpPullbackLookbackBars);
   for(int b=3;b<=pb_lookback;b++)
     {
      double bl=iLow(_Symbol,setup_tf,b);
      if(bl>0.0 && bl<pb_impulse_low) pb_impulse_low=bl;
     }
   pb_impulse_high=iHigh(_Symbol,setup_tf,3);
   double pb_impulse_low_sell=pb_impulse_low;
   double pb_impulse_high_sell=0.0;
   for(int b=3;b<=pb_lookback;b++)
     {
      double bh=iHigh(_Symbol,setup_tf,b);
      if(bh>0.0 && bh>pb_impulse_high_sell) pb_impulse_high_sell=bh;
     }
   double pb_leg_buy=pb_impulse_high-pb_impulse_low;
   double pb_leg_sell=pb_impulse_high_sell-pb_impulse_low_sell;
   double pb_retrace_buy=(pb_leg_buy>0.0 ? (pb_impulse_high-close1)/pb_leg_buy*100.0 : 999.0);
   double pb_retrace_sell=(pb_leg_sell>0.0 ? (close1-pb_impulse_low_sell)/pb_leg_sell*100.0 : 999.0);
   double pb_swing_low=MathMin(low1,low2);
   double pb_swing_high=MathMax(high1,high2);
   double sl_pullback_buy=GetStructuralSLDistance(atr_setup[0],entry_probe_buy,pb_swing_low,ORDER_TYPE_BUY,STRAT_PULLBACK,profile);
   double sl_pullback_sell=GetStructuralSLDistance(atr_setup[0],entry_probe_sell,pb_swing_high,ORDER_TYPE_SELL,STRAT_PULLBACK,profile);
   double sl_momentum_buy=GetStructuralSLDistance(atr_setup[0],entry_probe_buy,low2,ORDER_TYPE_BUY,STRAT_MOMENTUM,profile);
   double sl_momentum_sell=GetStructuralSLDistance(atr_setup[0],entry_probe_sell,high2,ORDER_TYPE_SELL,STRAT_MOMENTUM,profile);

   bool breakout_buy=close_cur>donchian_high && macro_bullish && volume_ok && !crypto_extended;
   bool breakout_sell=close_cur<donchian_low && macro_bearish && volume_ok && !crypto_extended;
   if(ModeAllowsStrategy(STRAT_BREAKOUT) && StrategyAllowedForProfile(STRAT_BREAKOUT,profile,g_market_regime) && (g_market_class!=MARKET_CRYPTO || CryptoProfileAllowsStrategy(STRAT_BREAKOUT,profile,g_market_regime,crypto_regime)) && (is_trending || InpStrategyMode==MODE_BREAKOUT_ONLY || InpStrategyMode==MODE_PORTFOLIO_RANKED))
     {
      if(breakout_buy && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_BUY,btc_bull,btc_bear,btc_strength)) AddCandidate(cands,ORDER_TYPE_BUY,"Breakout BUY",StringFormat("Breakout | profile %s",profile_name),ScoreSignalContext("Breakout BUY",macro_bullish,macro_bearish,is_trending,spread,sl_breakout_buy,now),sl_breakout_buy);
      if(breakout_sell && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_SELL,btc_bull,btc_bear,btc_strength)) AddCandidate(cands,ORDER_TYPE_SELL,"Breakout SELL",StringFormat("Breakout | profile %s",profile_name),ScoreSignalContext("Breakout SELL",macro_bullish,macro_bearish,is_trending,spread,sl_breakout_sell,now),sl_breakout_sell);
     }

   //--- VRAI PULLBACK : impulsion nette -> retracement -> bougie de reprise.
   double impulse_body3=MathAbs(iClose(_Symbol,setup_tf,3)-iOpen(_Symbol,setup_tf,3));
   bool pb_impulse_buy=(pb_leg_buy>=InpPullbackMinImpulseATR*atr_setup[0] &&
                        iClose(_Symbol,setup_tf,3)>iOpen(_Symbol,setup_tf,3) &&
                        iClose(_Symbol,setup_tf,3)>iClose(_Symbol,setup_tf,4) &&
                        impulse_body3>=InpPullbackMinImpulseATR*atr_setup[0]);
   bool pb_impulse_sell=(pb_leg_sell>=InpPullbackMinImpulseATR*atr_setup[0] &&
                         iClose(_Symbol,setup_tf,3)<iOpen(_Symbol,setup_tf,3) &&
                         iClose(_Symbol,setup_tf,3)<iClose(_Symbol,setup_tf,4) &&
                         impulse_body3>=InpPullbackMinImpulseATR*atr_setup[0]);
   bool pb_retrace_buy_ok=(pb_retrace_buy>=InpPullbackMinRetracePct && pb_retrace_buy<=InpPullbackMaxRetracePct);
   bool pb_retrace_sell_ok=(pb_retrace_sell>=InpPullbackMinRetracePct && pb_retrace_sell<=InpPullbackMaxRetracePct);
   double pb_confirm_body=MathAbs(close1-open1);
   bool pb_buy=macro_bullish && pb_impulse_buy && pb_retrace_buy_ok &&
               close2<open2 && close1>open1 &&
               pb_confirm_body>=InpPullbackMinConfirmBodyATR*atr_setup[0] &&
               close1>close2 && close1>pb_impulse_low && close1<pb_impulse_high;
   bool pb_sell=macro_bearish && pb_impulse_sell && pb_retrace_sell_ok &&
                close2>open2 && close1<open1 &&
                pb_confirm_body>=InpPullbackMinConfirmBodyATR*atr_setup[0] &&
                close1<close2 && close1<pb_impulse_high_sell && close1>pb_impulse_low_sell;
   if(ModeAllowsStrategy(STRAT_PULLBACK) && StrategyAllowedForProfile(STRAT_PULLBACK,profile,g_market_regime) && (g_market_class!=MARKET_CRYPTO || CryptoProfileAllowsStrategy(STRAT_PULLBACK,profile,g_market_regime,crypto_regime)) && (InpStrategyMode==MODE_TREND_ONLY || InpStrategyMode==MODE_ADAPTIVE_REGIME || InpStrategyMode==MODE_PORTFOLIO_RANKED))
     {
      if(pb_buy && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_BUY,btc_bull,btc_bear,btc_strength))
        {
         double q=ScoreSignalContext("Pullback BUY",macro_bullish,macro_bearish,is_trending,spread,sl_pullback_buy,now);
         q+=MathMin(8.0,MathMax(0.0,(pb_retrace_buy-20.0)/50.0*8.0));
         AddCandidate(cands,ORDER_TYPE_BUY,"Pullback BUY",StringFormat("Impulsion + retracement %.0f%% + confirmation | swing SL",pb_retrace_buy),q,sl_pullback_buy);
        }
      if(pb_sell && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_SELL,btc_bull,btc_bear,btc_strength))
        {
         double q=ScoreSignalContext("Pullback SELL",macro_bullish,macro_bearish,is_trending,spread,sl_pullback_sell,now);
         q+=MathMin(8.0,MathMax(0.0,(pb_retrace_sell-20.0)/50.0*8.0));
         AddCandidate(cands,ORDER_TYPE_SELL,"Pullback SELL",StringFormat("Impulsion + retracement %.0f%% + confirmation | swing SL",pb_retrace_sell),q,sl_pullback_sell);
        }
     }

   if(g_market_class==MARKET_CRYPTO && InpCryptoSweepEnabled &&
      ModeAllowsStrategy(STRAT_SWEEP) && StrategyAllowedForProfile(STRAT_SWEEP,profile,g_market_regime) &&
      CryptoProfileAllowsStrategy(STRAT_SWEEP,profile,g_market_regime,crypto_regime) &&
      (InpStrategyMode==MODE_ADAPTIVE_REGIME || InpStrategyMode==MODE_PORTFOLIO_RANKED))
     {
      ENUM_ORDER_TYPE crypto_sweep_type=ORDER_TYPE_BUY;
      double crypto_sweep_entry=0.0,crypto_sweep_sl=0.0,crypto_sweep_tp=0.0;
      if(CryptoSweepSignal(crypto_sweep_type,crypto_sweep_entry,crypto_sweep_sl,crypto_sweep_tp))
        {
         bool macro_ok=(!InpBlockCryptoAgainstMacro ||
                        (crypto_sweep_type==ORDER_TYPE_BUY ? !macro_bearish : !macro_bullish));
         double crypto_sweep_dist=MathAbs(crypto_sweep_entry-crypto_sweep_sl);
         if(macro_ok && crypto_sweep_dist>0.0 &&
            CryptoBTCAlignedForTrade(profile,crypto_sweep_type,btc_bull,btc_bear,btc_strength))
            AddCandidate(cands,crypto_sweep_type,
                         (crypto_sweep_type==ORDER_TYPE_BUY ? "Crypto Sweep BUY" : "Crypto Sweep SELL"),
                         StringFormat("Sweep/reclaim confirme | TP %.2fR",InpCryptoSweepTP_R),
                         ScoreSignalContext("Crypto Sweep",macro_bullish,macro_bearish,is_trending,spread,crypto_sweep_dist,now),
                         crypto_sweep_dist);
        }
     }

   bool sweep_sell=high1>donchian_high && close_cur<donchian_high && close_cur<open1;
   bool sweep_buy=low1<donchian_low && close_cur>donchian_low && close_cur>open1;
   if(g_market_class!=MARKET_CRYPTO && ModeAllowsStrategy(STRAT_SWEEP) && StrategyAllowedForProfile(STRAT_SWEEP,profile,g_market_regime) && (InpStrategyMode==MODE_ADAPTIVE_REGIME || InpStrategyMode==MODE_PORTFOLIO_RANKED))
     {
      double entry_buy=SymbolInfoDouble(_Symbol,SYMBOL_ASK), entry_sell=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double sl_sweep_buy=GetSweepSLDistance(atr_setup[0],low1,entry_buy,ORDER_TYPE_BUY,profile);
      double sl_sweep_sell=GetSweepSLDistance(atr_setup[0],high1,entry_sell,ORDER_TYPE_SELL,profile);
      bool sweep_sell_ok = sweep_sell;
      bool sweep_buy_ok = sweep_buy;
      if(g_market_class==MARKET_CRYPTO && InpBlockCryptoAgainstMacro)
        {
         // Prop desk: pas de mean-reversion agressive contre la macro dominante
         if(macro_bullish) sweep_sell_ok = false;
         if(macro_bearish) sweep_buy_ok = false;
        }
      if(sweep_sell_ok) AddCandidate(cands,ORDER_TYPE_SELL,"Sweep SELL","Sweep + reintegration",ScoreSignalContext("Sweep SELL",macro_bullish,macro_bearish,is_trending,spread,sl_sweep_sell,now),sl_sweep_sell);
      if(sweep_buy_ok) AddCandidate(cands,ORDER_TYPE_BUY,"Sweep BUY","Sweep + reintegration",ScoreSignalContext("Sweep BUY",macro_bullish,macro_bearish,is_trending,spread,sl_sweep_buy,now),sl_sweep_buy);
     }

   // Momentum continuation : impulsion nette puis poursuite sans nouveau breakout Donchian.
   double body2=MathAbs(close2-open2);
   bool mom_buy=macro_bullish && close2>open2 && body2>=0.70*atr_setup[0] && close_cur>close2 && close_cur>open1 && close_cur<donchian_high && !crypto_extended;
   bool mom_sell=macro_bearish && close2<open2 && body2>=0.70*atr_setup[0] && close_cur<close2 && close_cur<open1 && close_cur>donchian_low && !crypto_extended;
   if(ModeAllowsStrategy(STRAT_MOMENTUM) && StrategyAllowedForProfile(STRAT_MOMENTUM,profile,g_market_regime) && (g_market_class!=MARKET_CRYPTO || CryptoProfileAllowsStrategy(STRAT_MOMENTUM,profile,g_market_regime,crypto_regime)))
     {
      if(mom_buy)
        {
         string crypto_dir_reason="";
         bool dir_ok=CryptoDirectionConfirmed(setup_tf,ORDER_TYPE_BUY,crypto_dir_reason) && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_BUY,btc_bull,btc_bear,btc_strength);
         if(dir_ok)
            AddCandidate(cands,ORDER_TYPE_BUY,"Momentum BUY","Impulsion + continuation"+(g_market_class==MARKET_CRYPTO ? " | "+crypto_dir_reason : ""),ScoreSignalContext("Momentum BUY",macro_bullish,macro_bearish,is_trending,spread,sl_momentum_buy,now),sl_momentum_buy);
         else if(g_market_class==MARKET_CRYPTO && InpEnableCryptoDebugLogs)
            NotifyEvent("CRYPTO_DIRECTION_BLOCK",StringFormat("Momentum BUY | %s",crypto_dir_reason));
        }
      if(mom_sell)
        {
         string crypto_dir_reason="";
         bool dir_ok=CryptoDirectionConfirmed(setup_tf,ORDER_TYPE_SELL,crypto_dir_reason) && CryptoBTCAlignedForTrade(profile,ORDER_TYPE_SELL,btc_bull,btc_bear,btc_strength);
         if(dir_ok)
            AddCandidate(cands,ORDER_TYPE_SELL,"Momentum SELL","Impulsion + continuation"+(g_market_class==MARKET_CRYPTO ? " | "+crypto_dir_reason : ""),ScoreSignalContext("Momentum SELL",macro_bullish,macro_bearish,is_trending,spread,sl_momentum_sell,now),sl_momentum_sell);
         else if(g_market_class==MARKET_CRYPTO && InpEnableCryptoDebugLogs)
            NotifyEvent("CRYPTO_DIRECTION_BLOCK",StringFormat("Momentum SELL | %s",crypto_dir_reason));
        }
     }

   int best=BestCandidateIndex(cands);
   if(best>=0)
     {
      SignalCandidate chosen=cands[best];
      g_selected_signal_risk_factor=(InpEnableSignalRanking ? chosen.risk_factor : 1.0);
      if(StringFind(chosen.engine,"Crypto Sweep")>=0 && InpRiskPerTradePct>0.0)
         g_selected_signal_risk_factor=MathMin(g_selected_signal_risk_factor,InpCryptoSweepRiskPct/InpRiskPerTradePct);
      NotifyEvent("SIGNAL_RANKED",StringFormat("[%s/%s] %s | score %.1f | risk x%.2f | setupTF %s | macroTF %s | regime %s | ATRrel %.2f | %s",profile_name,GradeToString(chosen.grade),chosen.engine,chosen.score,g_selected_signal_risk_factor,EnumToString(setup_tf),EnumToString(macro_tf),MarketRegimeToString(g_market_regime),g_relative_atr_ratio,chosen.reason));
      ExecuteTrade(chosen.order_type,chosen.engine,StringFormat("[%s/%s] score %.1f | %s",profile_name,GradeToString(chosen.grade),chosen.score,chosen.reason),chosen.sl_dist);
      g_selected_signal_risk_factor=1.0;
     }
   else if(ArraySize(cands)>0) NotifyEvent("SIGNAL_NO_TRADE","Candidats non exploitables");

  }

//+------------------------------------------------------------------+
//| PROPFIRMGUARD RUNTIME (FILE_COMMON\\PropFirmGuard)               |
//+------------------------------------------------------------------+
string PfgReadCommonText(string relative_name)
  {
   int h=FileOpen("PropFirmGuard\\"+relative_name,FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h==INVALID_HANDLE) return "";
   string out="";
   while(!FileIsEnding(h))
     {
      string line=FileReadString(h);
      if(out!="") out+="\n";
      out+=line;
     }
   FileClose(h);
   return out;
  }

string PfgJsonString(string obj,string key)
  {
   string token="\""+key+"\""; int p=StringFind(obj,token); if(p<0) return "";
   p=StringFind(obj,":",p+StringLen(token)); if(p<0) return "";
   int q1=StringFind(obj,"\"",p+1); if(q1<0) return "";
   int q2=StringFind(obj,"\"",q1+1); if(q2<0) return "";
   return StringSubstr(obj,q1+1,q2-q1-1);
  }

int PfgJsonInt(string obj,string key,int fallback)
  {
   string token="\""+key+"\""; int p=StringFind(obj,token); if(p<0) return fallback;
   p=StringFind(obj,":",p+StringLen(token)); if(p<0) return fallback;
   int i=p+1; while(i<StringLen(obj) && StringGetCharacter(obj,i)==' ') i++;
   string num="";
   while(i<StringLen(obj))
     {
      ushort c=StringGetCharacter(obj,i);
      if((c>='0' && c<='9') || c=='-') {num+=CharToString((uchar)c); i++;} else break;
     }
   if(num=="") return fallback; return (int)StringToInteger(num);
  }

double PfgJsonDouble(string obj,string key,double fallback)
  {
   string token="\""+key+"\""; int p=StringFind(obj,token); if(p<0) return fallback;
   p=StringFind(obj,":",p+StringLen(token)); if(p<0) return fallback;
   int i=p+1; while(i<StringLen(obj) && StringGetCharacter(obj,i)==' ') i++;
   string num="";
   while(i<StringLen(obj))
     {
      ushort c=StringGetCharacter(obj,i);
      if((c>='0' && c<='9') || c=='-' || c=='.') {num+=CharToString((uchar)c); i++;} else break;
     }
   if(num=="") return fallback; return StringToDouble(num);
  }

bool PfgJsonBool(string obj,string key,bool fallback,bool &found)
  {
   found=false; string token="\""+key+"\""; int p=StringFind(obj,token); if(p<0) return fallback;
   p=StringFind(obj,":",p+StringLen(token)); if(p<0) return fallback;
   string tail=PfgUpper(StringSubstr(obj,p+1,12));
   if(StringFind(tail,"TRUE")>=0) {found=true; return true;}
   if(StringFind(tail,"FALSE")>=0) {found=true; return false;}
   return fallback;
  }

bool PfgArrayContains(string obj,string key,string wanted)
  {
   string token="\""+key+"\""; int p=StringFind(obj,token); if(p<0) return false;
   int a=StringFind(obj,"[",p); int b=(a>=0 ? StringFind(obj,"]",a) : -1); if(a<0 || b<0) return false;
   string arr=StringSubstr(obj,a,b-a+1);
   return StringFind(arr,"\""+wanted+"\"")>=0 || StringFind(arr,"\"*\"")>=0;
  }

bool PfgIsoDigits(const string text,int start,int count)
  {
   if(start<0 || count<=0 || start+count>StringLen(text)) return false;
   for(int i=start;i<start+count;i++)
     {
      ushort c=StringGetCharacter(text,i);
      if(c<'0' || c>'9') return false;
     }
   return true;
  }

datetime PfgParseIsoUtc(string iso)
  {
   StringTrimLeft(iso);
   StringTrimRight(iso);
   int len=StringLen(iso);
   if(len<19) return 0;
   if(!PfgIsoDigits(iso,0,4) || StringGetCharacter(iso,4)!='-' ||
      !PfgIsoDigits(iso,5,2) || StringGetCharacter(iso,7)!='-' ||
      !PfgIsoDigits(iso,8,2) ||
      (StringGetCharacter(iso,10)!='T' && StringGetCharacter(iso,10)!=' ') ||
      !PfgIsoDigits(iso,11,2) || StringGetCharacter(iso,13)!=':' ||
      !PfgIsoDigits(iso,14,2) || StringGetCharacter(iso,16)!=':' ||
      !PfgIsoDigits(iso,17,2)) return 0;

   string base=StringSubstr(iso,0,10)+" "+StringSubstr(iso,11,8);
   StringReplace(base,"-",".");
   datetime parsed=StringToTime(base);
   if(parsed<=0 || TimeToString(parsed,TIME_DATE|TIME_SECONDS)!=base) return 0;

   int pos=19;
   if(pos<len && StringGetCharacter(iso,pos)=='.')
     {
      int fraction_start=++pos;
      while(pos<len)
        {
         ushort c=StringGetCharacter(iso,pos);
         if(c<'0' || c>'9') break;
         pos++;
        }
      if(pos==fraction_start) return 0;
     }

   int offset_seconds=0;
   if(pos<len)
     {
      ushort zone=StringGetCharacter(iso,pos);
      if(zone=='Z' || zone=='z')
        {
         pos++;
        }
      else if(zone=='+' || zone=='-')
        {
         if(pos+6!=len || !PfgIsoDigits(iso,pos+1,2) ||
            StringGetCharacter(iso,pos+3)!=':' || !PfgIsoDigits(iso,pos+4,2)) return 0;
         int hours=(int)StringToInteger(StringSubstr(iso,pos+1,2));
         int minutes=(int)StringToInteger(StringSubstr(iso,pos+4,2));
         if(hours>14 || minutes>59 || (hours==14 && minutes!=0)) return 0;
         offset_seconds=(hours*60+minutes)*60;
         if(zone=='-') offset_seconds=-offset_seconds;
         pos=len;
        }
      else return 0;
     }
   if(pos!=len) return 0;
   datetime utc=parsed-offset_seconds;
   return (utc>0 ? utc : 0);
  }

int PfgApplicableAlertCount(string &summary)
  {
   summary=""; string txt=PfgReadCommonText("risk_alerts.json");
   if(txt=="") {summary="runtime risk_alerts.json absent"; return 0;}
   int count=0,pos=0; string firm=PropFirmName(g_prop_firm); string profile=PropProfileName(g_prop_profile);
   while(true)
     {
      int cid=StringFind(txt,"\"change_id\"",pos); if(cid<0) break;
      int a=cid; while(a>0 && StringGetCharacter(txt,a)!='{') a--;
      int b=StringFind(txt,"}",cid); if(b<0) break;
      string obj=StringSubstr(txt,a,b-a+1); pos=b+1;
      string pf=PfgJsonString(obj,"prop_firm_id");
      bool firm_ok=(pf=="*" || pf==firm);
      bool prof_ok=(g_prop_profile==PFG_PROFILE_UNKNOWN ? firm_ok : PfgArrayContains(obj,"profile_ids",profile));
      if(firm_ok && prof_ok)
        {
         count++;
         if(summary=="") summary=PfgJsonString(obj,"classification_reason");
        }
     }
   if(count>0) summary=StringFormat("%s | %d alerte(s) applicable(s)",summary,count);
   return count;
  }

void RefreshPropFirmRulesRuntime()
  {
   g_pfg_rules_loaded=false;
   g_pfg_runtime_daily_loss_pct=-1.0;
   g_pfg_runtime_overall_loss_pct=-1.0;
   g_pfg_runtime_ea_allowed_known=false;
   g_pfg_runtime_ea_allowed=true;
   g_pfg_runtime_news_rule="";
   // A firm-wide fallback can mix mutually exclusive programme rules.  Keep
   // the conservative internal baseline until the account profile is known.
   if(g_prop_profile==PFG_PROFILE_UNKNOWN) return;
   string txt=PfgReadCommonText("rules.json");
   if(txt=="") return;
   int pos=0; string firm=PropFirmName(g_prop_firm); string profile=PropProfileName(g_prop_profile);
   while(true)
     {
      int ridpos=StringFind(txt,"\"rule_id\"",pos); if(ridpos<0) break;
      int a=ridpos; while(a>0 && StringGetCharacter(txt,a)!='{') a--;
      int b=StringFind(txt,"}",ridpos); if(b<0) break;
      string obj=StringSubstr(txt,a,b-a+1); pos=b+1;
      string pf=PfgJsonString(obj,"prop_firm_id");
      if(!(pf==firm || pf=="*")) continue;
      if(g_prop_profile!=PFG_PROFILE_UNKNOWN && !PfgArrayContains(obj,"profile_ids",profile)) continue;
      string rid=PfgJsonString(obj,"rule_id");
      if(rid=="max_daily_loss_pct")
        {double v=PfgJsonDouble(obj,"value",-1.0); if(v>0.0) {g_pfg_runtime_daily_loss_pct=v; g_pfg_rules_loaded=true;}}
      else if(rid=="max_loss_pct")
        {double v=PfgJsonDouble(obj,"value",-1.0); if(v>0.0) {g_pfg_runtime_overall_loss_pct=v; g_pfg_rules_loaded=true;}}
      else if(rid=="ea_allowed")
        {bool found=false; bool v=PfgJsonBool(obj,"value",true,found); if(found) {g_pfg_runtime_ea_allowed_known=true; g_pfg_runtime_ea_allowed=v; g_pfg_rules_loaded=true;}}
      else if(rid=="news_restriction")
        {
         string v=PfgJsonString(obj,"value");
         if(v!="")
           {
            g_pfg_runtime_news_rule=v; g_pfg_rules_loaded=true;
            if(v=="NONE") g_prop_news_policy=PROP_NEWS_NONE;
            else if(PfgContains(v,"REWARD_SHARE") || PfgContains(v,"5MIN")) g_prop_news_policy=PROP_NEWS_FUNDEDNEXT_REWARD_5MIN;
            else if(PfgContains(v,"2MIN") || PfgContains(v,"TARGETED")) g_prop_news_policy=PROP_NEWS_FTMO_RESTRICTED_2MIN;
           }
        }
     }
   if(g_pfg_runtime_ea_allowed_known && !g_pfg_runtime_ea_allowed)
     {g_prop_execution_authorized=false; g_prop_execution_reason="PROPFIRMGUARD: EA INTERDIT PAR LE DERNIER BASELINE";}
  }

bool PfgHealthStale(string &why)
  {
   why=""; string txt=PfgReadCommonText("health.json");
   if(txt=="") {why="health.json absent"; return true;}
   string health_status=PfgJsonString(txt,"status");
   if(health_status=="FAILED") {why="watcher/source status FAILED"; return true;}
   string last=PfgJsonString(txt,"last_cycle_utc"); int stale=PfgJsonInt(txt,"stale_after_seconds",10800);
   datetime t=PfgParseIsoUtc(last); datetime now=(MQLInfoInteger(MQL_TESTER) ? TimeCurrent() : TimeGMT());
   if(t<=0) {why="last_cycle_utc illisible"; return true;}
   if((long)(now-t)>stale) {why=StringFormat("watcher stale %d sec",(int)(now-t)); return true;}
   return false;
  }

void RefreshPropFirmGuardRuntime()
  {
   if(!InpEnablePropFirmRuntimeAlerts) {g_pfg_runtime_alert_count=0; g_pfg_runtime_stale=false; g_pfg_badge.ApplyRiskState(0,""); return;}
   datetime now=TimeCurrent(); int interval=(int)MathMax(5,InpPropFirmRuntimeScanSeconds);
   if(g_pfg_last_runtime_scan!=0 && now-g_pfg_last_runtime_scan<interval) {g_pfg_badge.Pulse(); return;}
   g_pfg_last_runtime_scan=now;
   string alert_summary="",stale_reason="";
   RefreshPropFirmRulesRuntime();
   g_pfg_runtime_alert_count=PfgApplicableAlertCount(alert_summary);
   g_pfg_runtime_stale=PfgHealthStale(stale_reason);
   int visible=g_pfg_runtime_alert_count+(g_pfg_runtime_stale ? 1 : 0);
   g_pfg_runtime_summary=(visible<=0 ? "OK" : (g_pfg_runtime_stale ? stale_reason : alert_summary));
   g_pfg_badge.ApplyRiskState(visible,g_pfg_runtime_summary);
  }

long FindPortfolioChart(string symbol,ENUM_TIMEFRAMES period)
  {
   long chart=ChartFirst();
   while(chart>=0)
     {
      if(ChartSymbol(chart)==symbol && ChartPeriod(chart)==period) return chart;
      chart=ChartNext(chart);
     }
   return -1;
  }

void BootstrapPortfolioCharts()
  {
   if(!InpBootstrapPortfolioCharts || MQLInfoInteger(MQL_TESTER)) return;
   string lock=StringFormat("PFG_BOOTSTRAP_%I64d",AccountInfoInteger(ACCOUNT_LOGIN));
   datetime now=TimeLocal();
   if(GlobalVariableCheck(lock) && now-(datetime)GlobalVariableGet(lock)<120) return;
   GlobalVariableSet(lock,(double)now);

   const string template_name="Guardian_D017_Observation.tpl";
   if(!ChartSaveTemplate(0,template_name))
     {
      PrintFormat("[PFG BOOTSTRAP] sauvegarde modèle impossible, erreur=%d",GetLastError());
      return;
     }
   string symbols[];
   int count=StringSplit(InpBootstrapPortfolioSymbols,',',symbols);
   for(int i=0;i<count;i++)
     {
      string symbol=symbols[i]; StringTrimLeft(symbol); StringTrimRight(symbol);
      if(symbol=="" || symbol==_Symbol) continue;
      if(!SymbolSelect(symbol,true))
        {PrintFormat("[PFG BOOTSTRAP] symbole indisponible: %s",symbol); continue;}
      long chart=FindPortfolioChart(symbol,PERIOD_M15);
      if(chart<0) chart=ChartOpen(symbol,PERIOD_M15);
      if(chart<=0)
        {PrintFormat("[PFG BOOTSTRAP] ouverture impossible: %s erreur=%d",symbol,GetLastError()); continue;}
      if(!ChartApplyTemplate(chart,template_name))
        PrintFormat("[PFG BOOTSTRAP] application modèle impossible: %s erreur=%d",symbol,GetLastError());
      else
        PrintFormat("[PFG BOOTSTRAP] Guardian observation chargé sur %s M15",symbol);
     }
  }

//+------------------------------------------------------------------+
//| INITIALISATION                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_ea_start_time = TimeCurrent();
   ResolvePropFirmContext();
   g_detected_base_cap = DetectInitialCapitalRobust();
   if(InpClassicSessionStartUTC<0.0 || InpClassicSessionStartUTC>=24.0 ||
      InpClassicSessionEndUTC<=0.0 || InpClassicSessionEndUTC>24.0 ||
      InpClassicSessionStartUTC==InpClassicSessionEndUTC)
     {
      Print("[ERREUR PARAMETRES] Fenêtre de session classique UTC invalide.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(MQLInfoInteger(MQL_TESTER))
     {
      long login=AccountInfoInteger(ACCOUNT_LOGIN);
      GlobalVariableDel(StringFormat("PFG_GUARD_V112_DAYKEY_%I64d",login));
      GlobalVariableDel(StringFormat("PFG_GUARD_V112_START_BAL_%I64d",login));
      GlobalVariableDel(StringFormat("PFG_GUARD_V112_PEAK_EOD_%I64d",login));
      GlobalVariableDel(AccountStateGV("LOSSES"));
      GlobalVariableDel(AccountStateGV("COOLDOWN"));
     }
   // Nom stable : le Strategy Tester n'utilise pas le même login que le compte FTMO.
   g_news_csv_name = "Guardian_PropFirm_NewsCalendar.csv";
   g_ledger_csv_name = StringFormat("Guardian_Ledger_%I64d.csv",AccountInfoInteger(ACCOUNT_LOGIN));
   g_pfg_badge.Init(StringFormat("%I64d",AccountInfoInteger(ACCOUNT_LOGIN)));
   RefreshPropFirmGuardRuntime();
   g_news_cache_last_refresh = 0;
   RefreshNativeNewsCalendar();
   g_auto_magic = GenerateAutoMagicNumber(_Symbol);
   g_trade.SetExpertMagicNumber(g_auto_magic);
   g_trade.SetAsyncMode(false);

   g_market_class = DetectMarketClass(_Symbol);
   g_market_profile_name = MarketClassToString(g_market_class);
   ENUM_TIMEFRAMES init_setup_tf = GetProfileSetupTF("PORTFOLIO");
   ENUM_TIMEFRAMES init_macro_tf = GetProfileMacroTF();
   g_atr_handle       = iATR(_Symbol, init_setup_tf, InpATR_Period);
   g_adx_handle       = iADX(_Symbol, init_setup_tf, InpADX_Period);
   g_macro_ema_handle = iMA(_Symbol, init_macro_tf, InpMacroEMA_Period, 0, MODE_EMA, PRICE_CLOSE);

   g_instance_chart_id=ChartID();
   RefreshInstanceOwnership(true);
   if(!MQLInfoInteger(MQL_TESTER) && InpEnableAccountWideManualGuardian)
     {
      int timer_ms=MathMax(50,InpManualGuardianTimerMs);
      if(!EventSetMillisecondTimer(timer_ms))
         PrintFormat("[MANUAL_GUARD] EventSetMillisecondTimer(%d) echec=%d | fallback OnTick actif",timer_ms,GetLastError());
     }
   if(InpAutoAddTPToManual)
      Print("[MANUAL_GUARD] v11.15: InpAutoAddTPToManual est legacy; aucun TP broker integral ne sera ajoute automatiquement.");

   if(g_atr_handle == INVALID_HANDLE || g_adx_handle == INVALID_HANDLE || g_macro_ema_handle == INVALID_HANDLE)
     {
      Print("[ERREUR FATALE] Impossible de charger les indicateurs.");
      return INIT_FAILED;
     }

   GuardianHeartbeat();
   UpdateMarketProfile();
   g_last_signal_bar_time = iTime(_Symbol, GetProfileSetupTF("PORTFOLIO"), 0);
   g_account_cooldown_to = GetAccountCooldownUntil();
   g_consecutive_losses = GetAccountConsecutiveLosses();
   
   PrintFormat("==================================================");
   PrintFormat(" Guardian D017 / PropFirmAuto v11.16 MOMENTUM PROD");
   PrintFormat(" PROD PROFILE : D017 MOMENTUM ONLY | session 07-17 UTC | time-stop OFF | Breakout/Pullback/Sweep LOCKED OFF");
   PrintFormat(" Capital Référence : %.0f $ | Mode : %s", g_detected_base_cap, EnumToString(InpStrategyMode));
   PrintFormat(" Prop firm : %s | Profil : %s | News : %s",PropFirmName(g_prop_firm),PropProfileName(g_prop_profile),PropNewsPolicyName());
   PrintFormat(" Detection : %s",g_prop_detection_reason);
   PrintFormat(" Execution EA : %s | %s",(g_prop_execution_authorized ? "AUTORISEE" : "OBSERVATION UNIQUEMENT"),g_prop_execution_reason);
   PrintFormat(" Seuil fermeture Guardian : %.2f%%", InpGuardianDailyStopPct);
   PrintFormat(" Risque dynamique : %s | Exposition devise : SUPPRIMEE", (InpEnableDailyRiskScaling ? "ACTIF" : "INACTIF"));
   PrintFormat(" Profil marché : %s | Régime : %s | Ranking : %s", MarketClassToString(g_market_class), MarketRegimeToString(g_market_regime), (InpEnableSignalRanking ? "ON" : "OFF"));
   PrintFormat(" Setup TF : %s | Macro TF : %s | Crypto engine : %s", EnumToString(GetProfileSetupTF("PORTFOLIO")), EnumToString(GetProfileMacroTF()), (InpCryptoRegimeEngine ? "ON" : "OFF"));
   PrintFormat(" Owners : symbole=%s | manuel account-wide=%s | chart=%I64d",(g_symbol_instance_owner?"OWNER":"STANDBY"),(g_manual_guard_owner?"OWNER":"STANDBY"),g_instance_chart_id);
   PrintFormat(" Session classique UTC : %.2f -> %.2f",InpClassicSessionStartUTC,InpClassicSessionEndUTC);
      PrintFormat("==================================================");
   NotifyEvent("EA_STARTED", StringFormat("Capital %.0f | Guardian %.2f%% | manuel: SL %.2fATR | risque max %.2f$ | BE %.2fR | TP %.2fR/%.0f%% | risque ouvert max %.2f%% | plancher risque effectif %.2f$", g_detected_base_cap, InpGuardianDailyStopPct, InpManualSL_ATR_Mult, GetManualMaxRiskUSD(), InpManualBE_Trigger_R, InpManualTP_R, InpManualTP_ClosePercent, InpMaxOpenAccountRiskPct, EffectiveMinTradeRiskUSD()));
   BootstrapPortfolioCharts();
   
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) 
  {
   if(!MQLInfoInteger(MQL_TESTER)) EventKillTimer();
   ReleaseInstanceOwnership();
   if(g_atr_handle != INVALID_HANDLE) IndicatorRelease(g_atr_handle); 
   if(g_adx_handle != INVALID_HANDLE) IndicatorRelease(g_adx_handle); 
   if(g_macro_ema_handle != INVALID_HANDLE) IndicatorRelease(g_macro_ema_handle);
   g_pfg_badge.Release();
   ReleaseGlobalTradeLock();
   Comment(""); 
  }

//+------------------------------------------------------------------+
//| TICK & HUD                                                       |
//+------------------------------------------------------------------+
void UpdateHUD()
  {
   string status = "🟢 NORMAL";
   if(g_snap.state == GUARDIAN_LOCKED) status = "🔴 BLOQUÉ (SEUIL ATTEINT)";
   else if(g_snap.state == GUARDIAN_FORCE_CLOSE) status = "🚨 FERMETURE D'URGENCE PROP FIRM";
   else if(g_snap.state == GUARDIAN_WARNING) status = "🟠 ATTENTION (MARGE PROCHE)";

   g_account_cooldown_to = GetAccountCooldownUntil();
   g_consecutive_losses = GetAccountConsecutiveLosses();

   double profit_total_pct = ((g_snap.equity - g_detected_base_cap) / g_detected_base_cap) * 100.0;
   double daily_loss_pct = (g_detected_base_cap > 0.0 ? (g_snap.daily_loss / g_detected_base_cap) * 100.0 : 0.0);
   double active_risk_pct = GetEffectiveRiskPercent();
   
   string hud = "==================================================\n";
   hud += "   GUARDIAN D017 / PROP FIRM AUTO v11.16 PROD       \n";
   hud += "   PROFILE FIGE : MOMENTUM | 07-17 UTC | TIME-STOP OFF\n";
   hud += "==================================================\n";
   hud += StringFormat(" Actif: %s | TF: %s (Macro: %s)\n", _Symbol, EnumToString(_Period), EnumToString(InpMacroTrendTF));
   hud += StringFormat(" Capital Référence : %.0f $\n", g_detected_base_cap);
   hud += StringFormat(" Prop firm         : %s\n",PropFirmName(g_prop_firm));
   hud += StringFormat(" Profil compte     : %s\n",PropProfileName(g_prop_profile));
   hud += StringFormat(" EA / execution    : %s\n",(g_prop_execution_authorized ? "🟢 AUTORISEE" : "🟠 OBSERVATION"));
   if(!g_prop_execution_authorized) hud += StringFormat(" Raison            : %s\n",g_prop_execution_reason);
   hud += StringFormat(" PropFirmGuard     : %s | règles %s\n",(g_pfg_runtime_alert_count==0 && !g_pfg_runtime_stale ? "🟢 OK" : "🔴 REVUE"),(g_pfg_rules_loaded ? "runtime" : "baseline interne"));
   hud += StringFormat(" Statut du Compte  : %s\n", status);
   hud += StringFormat(" Session           : %s\n", (IsInAllowedSession() ? "OUVERTE ✅" : "FERMÉE ⏸️"));
   { string nr=""; hud += StringFormat(" News / règle      : %s | %s\n", (IsNewsEntryBlocked(_Symbol,nr) ? "🔒 PROTEGEE" : "🟢 LIBRE"),PropNewsPolicyName()); }
   hud += "--------------------------------------------------\n";
   hud += StringFormat(" PnL Global        : %+.2f $ (%+.2f%%)\n", (g_snap.equity - g_detected_base_cap), profit_total_pct);
   hud += StringFormat(" Marge Journalière : %.2f $ restante\n", g_snap.daily_remaining_guardian);
   hud += StringFormat(" Perte Jour        : %.2f%% | Risque actif : %.2f%%\n", daily_loss_pct, active_risk_pct);
   hud += StringFormat(" Risque Ouvert     : %.2f $ (%.2f%%)\n", g_snap.open_risk_usd, (g_snap.open_risk_usd / g_detected_base_cap)*100.0);
   hud += StringFormat(" Profil Marché     : %s | Régime: %s | ATR rel: %.2f\n", MarketClassToString(g_market_class), MarketRegimeToString(g_market_regime), g_relative_atr_ratio);
   hud += StringFormat(" Setup TF          : %s | Macro TF: %s\n", EnumToString(GetProfileSetupTF("PORTFOLIO")), EnumToString(GetProfileMacroTF()));
   hud += StringFormat(" Profil détail      : %s\n", g_market_profile_name);
   hud += StringFormat(" Ranking Signaux   : %s\n", (InpEnableSignalRanking ? "ACTIF" : "INACTIF"));
   hud += StringFormat(" Instance symbole  : %s | chart %I64d\n",(g_symbol_instance_owner?"OWNER":"STANDBY"),g_instance_chart_id);
   hud += StringFormat(" Guardian manuel   : %s | compte entier | timer %d ms\n",(g_manual_guard_owner?"OWNER":"STANDBY"),InpManualGuardianTimerMs);
   int manual_count = 0;
   double manual_risk = 0.0;
   for(int mi = PositionsTotal()-1; mi >= 0; mi--)
     {
      ulong mt = PositionGetTicket(mi);
      if(mt == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 0) continue;
      manual_count++;
      manual_risk += CalculateOpenPositionRisk(mt);
     }
   hud += StringFormat(" Manuels           : %d | Risque: %.2f $ | Max/position: %.2f $\n", manual_count, manual_risk, GetManualMaxRiskUSD());
   hud += StringFormat(" Gestion manuelle  : SL %.2f ATR | BE +%.2fR | TP %.2fR / %.0f%%\n", InpManualSL_ATR_Mult, InpManualBE_Trigger_R, InpManualTP_R, InpManualTP_ClosePercent);
   hud += "==================================================\n";
   Comment(hud);
  }


bool CryptoSweepSignal(ENUM_ORDER_TYPE &signal_type, double &entry_price,
                       double &sl_price, double &tp_price)
  {
   if(!InpCryptoSweepEnabled || !IsSymbolCrypto(_Symbol)) return false;

   ENUM_TIMEFRAMES tf = InpCryptoSweepTF;
   int confirm_bars=MathMax(1,InpCryptoSweepConfirmBars);
   int sweep_index=confirm_bars+1;
   int need = MathMax(InpCryptoSweepLookback + sweep_index + 5, 40);

   MqlRates r[];
   ArraySetAsSeries(r,true);
   if(CopyRates(_Symbol,tf,0,need,r) < need) return false;

   double atr_buf[];
   ArraySetAsSeries(atr_buf,true);
   bool shared_atr=(tf==GetProfileSetupTF("") && InpATR_Period==14 && g_atr_handle!=INVALID_HANDLE);
   int h = (shared_atr ? g_atr_handle : iATR(_Symbol,tf,14));
   if(h == INVALID_HANDLE) return false;
   bool ok = (CopyBuffer(h,0,0,need,atr_buf) >= need);
   if(!shared_atr) IndicatorRelease(h);
   if(!ok || atr_buf[sweep_index] <= 0.0) return false;

   // Une seule décision par nouvelle bougie.
   static datetime last_signal_bar = 0;
   if(r[1].time == last_signal_bar) return false;

   double atr = atr_buf[sweep_index];
   double range = r[sweep_index].high - r[sweep_index].low;
   if(range <= 0.0) return false;

   // Niveaux de liquidité : plus haut / plus bas des N dernières bougies
   // précédant la bougie de sweep.
   double prior_high = -DBL_MAX;
   double prior_low  = DBL_MAX;
   int lb = MathMin(InpCryptoSweepLookback, need-sweep_index-1);
   for(int i=sweep_index+1; i<sweep_index+1+lb; i++)
     {
      prior_high = MathMax(prior_high,r[i].high);
      prior_low  = MathMin(prior_low,r[i].low);
     }

   double upper_wick = r[sweep_index].high-MathMax(r[sweep_index].open,r[sweep_index].close);
   double lower_wick = MathMin(r[sweep_index].open,r[sweep_index].close)-r[sweep_index].low;

   double atr_mult = range/atr;
   if(atr_mult < InpCryptoSweepMinATR || atr_mult > InpCryptoSweepMaxATR)
      return false;

   // SELL : sweep du high puis réintégration.
   if(r[sweep_index].high > prior_high &&
      r[sweep_index].close < prior_high &&
      upper_wick/range*100.0 >= InpCryptoSweepMinWickPct)
     {
      double reclaim = (r[sweep_index].high-r[sweep_index].close)/(r[sweep_index].high-prior_high);
      if(reclaim < InpCryptoSweepReclaimPct/100.0) return false;

      // Confirmations closes uniquement : aucun biais de bougie courante.
      for(int i=1;i<=confirm_bars;i++) if(r[i].high>r[sweep_index].high) return false;
      if(r[1].close>=prior_high) return false;

      entry_price = SymbolInfoDouble(_Symbol,SYMBOL_BID);
      if(entry_price <= 0.0) return false;

      sl_price = NormalizeTradePrice(_Symbol,
                    r[sweep_index].high + atr*InpCryptoSweepSL_ATR);
      double risk_dist = sl_price-entry_price;
      if(risk_dist <= 0.0) return false;

      tp_price = NormalizeTradePrice(_Symbol,
                    entry_price-risk_dist*InpCryptoSweepTP_R);
      signal_type = ORDER_TYPE_SELL;
      last_signal_bar = r[1].time;
      return true;
     }

   // BUY : sweep du low puis réintégration.
   if(r[sweep_index].low < prior_low &&
      r[sweep_index].close > prior_low &&
      lower_wick/range*100.0 >= InpCryptoSweepMinWickPct)
     {
      double reclaim = (r[sweep_index].close-r[sweep_index].low)/(prior_low-r[sweep_index].low);
      if(reclaim < InpCryptoSweepReclaimPct/100.0) return false;

      for(int i=1;i<=confirm_bars;i++) if(r[i].low<r[sweep_index].low) return false;
      if(r[1].close<=prior_low) return false;

      entry_price = SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      if(entry_price <= 0.0) return false;

      sl_price = NormalizeTradePrice(_Symbol,
                    r[sweep_index].low - atr*InpCryptoSweepSL_ATR);
      double risk_dist = entry_price-sl_price;
      if(risk_dist <= 0.0) return false;

      tp_price = NormalizeTradePrice(_Symbol,
                    entry_price+risk_dist*InpCryptoSweepTP_R);
      signal_type = ORDER_TYPE_BUY;
      last_signal_bar = r[1].time;
      return true;
     }

   return false;
  }

void OnTimer()
  {
   if(MQLInfoInteger(MQL_TESTER)) return;
   RefreshInstanceOwnership(false);
   if(!g_prop_execution_authorized) return;
   if(InpEnableAccountWideManualGuardian && InpAdoptManualTrades && g_manual_guard_owner)
      ManageAccountWideManualTrades();
  }

void OnTick()
  {
   RefreshInstanceOwnership(false);
   ProcessPendingClosedPositions();
   RefreshPropFirmGuardRuntime();
   RefreshNativeNewsCalendar();
   GuardianHeartbeat();
   UpdateHUD();
   if(!g_prop_execution_authorized) return; // mode diagnostic: aucune requête de trading
   if(g_symbol_instance_owner)
     {
      CheckFTMONewsProtection();
      CheckFridayWeekendProtection();
     }

   if(g_snap.state == GUARDIAN_FORCE_CLOSE || g_snap.state == GUARDIAN_LOCKED)
     {
      bool emergency_owner=(InpEmergencyCloseWholeAccount ? g_manual_guard_owner : g_symbol_instance_owner);
      if(!emergency_owner) return;
      if(TimeCurrent() - g_last_force_close_att >= 2)
        {
         g_last_force_close_att = TimeCurrent();
         if(IsMarketClosedBackoffActive()) return;
         bool emergency_session_known=false;
         bool emergency_session_open=IsDeclaredTradeSessionOpen(_Symbol,TimeCurrent(),emergency_session_known);
         if(emergency_session_known && !emergency_session_open) return;
         for(int i = PositionsTotal() - 1; i >= 0; i--)
           {
            ulong ticket = PositionGetTicket(i);
            if(ticket == 0) continue;
            bool is_our_symbol = (PositionGetString(POSITION_SYMBOL) == _Symbol);
            long magic = PositionGetInteger(POSITION_MAGIC);
            bool is_managed = (magic == (long)g_auto_magic || (InpAdoptManualTrades && magic == 0));
            if(!InpEmergencyCloseWholeAccount && !(is_our_symbol && is_managed)) continue;

            if(!g_trade.PositionClose(ticket))
              {
               PrintFormat("[EMERGENCY_CLOSE] Echec fermeture #%I64u : %s", ticket, g_trade.ResultRetcodeDescription());
               if(RegisterMarketClosedBackoff("EMERGENCY_CLOSE",ticket)) return;
              }
           }
        }
      return;
     }

   ManagePositionsAndManualTrades(); 

   // Les entrees AUTO appartiennent à une seule instance par symbole.
   if(!g_symbol_instance_owner) return;

   // Les entrees sont evaluees à chaque nouvelle bougie du timeframe de setup actif.
   datetime current_bar_time = iTime(_Symbol, GetProfileSetupTF("PORTFOLIO"), 0);
   if(current_bar_time != g_last_signal_bar_time)
     {
      // Une mise à jour bid/ask asynchrone peut produire momentanément un
      // spread nul ou croisé. Attendre le tick exécutable suivant sans
      // consommer la bougie évite une entrée impossible en conditions réelles.
      MqlTick entry_quote;
      if(!SymbolInfoTick(_Symbol,entry_quote) || entry_quote.bid<=0.0 || entry_quote.ask<=entry_quote.bid)
         return;
      g_last_signal_bar_time = current_bar_time;
      CheckSignals();
     }
  }
//+------------------------------------------------------------------+
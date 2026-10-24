<#import "footer.ftl" as loginFooter>
<#-- Базовый template.ftl Keycloak 26: шапка заменена на логотип SaveFlow, внизу карточки — возврат к обычному входу -->
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html class="${properties.kcHtmlClass!}"<#if realm.internationalizationEnabled> lang="${locale.currentLanguageTag}" dir="${(locale.rtl)?then('rtl','ltr')}"</#if>>

<head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="robots" content="noindex, nofollow">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${msg("loginTitle",(realm.displayName!''))}</title>
    <link rel="icon" type="image/png" href="${url.resourcesPath}/img/logo.png" />
    <#if properties.stylesCommon?has_content>
        <#list properties.stylesCommon?split(' ') as style>
            <link href="${url.resourcesCommonPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <script type="importmap">
        {
            "imports": {
                "rfc4648": "${url.resourcesCommonPath}/vendor/rfc4648/rfc4648.js"
            }
        }
    </script>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="module">
        import { startSessionPolling } from "${url.resourcesPath}/js/authChecker.js";

        startSessionPolling(
          "${url.ssoLoginInOtherTabsUrl?no_esc}"
        );
    </script>
</head>

<body class="sf-body">
<div class="sf-wrap">
    <div class="sf-brand">
        <img class="sf-logo" src="${url.resourcesPath}/img/logo.png" alt="" />
        <span>SaveFlow</span>
    </div>
    <div class="sf-card">
        <header class="sf-card-head">
            <#if realm.internationalizationEnabled  && locale.supported?size gt 1>
                <#-- Переключатель в одно нажатие. Коды вместо названий: Keycloak пишет названия на языке страницы («английский») -->
                <nav id="kc-locale" class="sf-lang" aria-label="${msg("languages")}">
                    <#list locale.supported as l>
                        <#if l.languageTag == locale.currentLanguageTag>
                            <span class="sf-lang-item sf-lang-current" aria-current="true" title="${l.label}">${l.languageTag?upper_case}</span>
                        <#else>
                            <a class="sf-lang-item" href="${l.url}" lang="${l.languageTag}" title="${l.label}">${l.languageTag?upper_case}</a>
                        </#if>
                    </#list>
                </nav>
            </#if>
        <#if !(auth?has_content && auth.showUsername() && !auth.showResetCredentials())>
            <h1 id="kc-page-title"><#nested "header"></h1>
        <#else>
            <#nested "show-username">
            <div id="kc-username">
                <label id="kc-attempted-username">${auth.attemptedUsername}</label>
                <a id="reset-login" href="${url.loginRestartFlowUrl}" aria-label="${msg("restartLoginTooltip")}">${msg("restartLoginTooltip")}</a>
            </div>
        </#if>
        </header>
        <div id="kc-content">
            <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
                <div class="sf-alert sf-alert-${message.type}" role="alert">${kcSanitize(message.summary)?no_esc}</div>
            </#if>

            <#nested "form">

            <#if auth?has_content && auth.showTryAnotherWayLink()>
                <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
                    <input type="hidden" name="tryAnotherWay" value="on"/>
                    <a href="#" id="try-another-way"
                       onclick="document.forms['kc-select-try-another-way-form'].requestSubmit();return false;">${msg("doTryAnotherWay")}</a>
                </form>
            </#if>

            <#nested "socialProviders">

            <#if displayInfo>
                <div id="kc-info"><#nested "info"></div>
            </#if>
        </div>

        <div class="sf-or" role="separator">${msg("orSeparator")}</div>
        <a id="sf-back" class="sf-btn sf-btn-ghost" href="javascript:history.back()">${msg("backToPanelLogin")}</a>

        <@loginFooter.content/>
    </div>
</div>
<script>
    // Панель может жить на любом адресе (5173, 8080, сервер), а Keycloak его не знает. Первый заход сюда — переход
    // с кнопки панели: её адрес приходит в referrer. Запоминаем его, чтобы кнопка работала и после неверного пароля.
    (function () {
        var key = "saveflow-panel-origin";
        try {
            var from = document.referrer ? new URL(document.referrer).origin : "";
            if (from && from !== location.origin) sessionStorage.setItem(key, from);
            var panel = sessionStorage.getItem(key);
            if (panel) document.getElementById("sf-back").href = panel + "/login";
        } catch (e) { /* без referrer кнопка просто вернёт назад по истории */ }
    })();
</script>
</body>
</html>
</#macro>

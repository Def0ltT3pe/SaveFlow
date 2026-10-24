<#import "template.ftl" as layout>
<#-- Подтверждение выхода: форма как в базовой теме Keycloak 26, кнопка — как «Войти» -->
<@layout.registrationLayout; section>
    <#if section = "header">
        ${msg("logoutConfirmTitle")}
    <#elseif section = "form">
        <p class="sf-text">${msg("logoutConfirmHeader")}</p>
        <form id="kc-logout-form" action="${url.logoutConfirmAction}" onsubmit="confirmLogout.disabled = true; return true;" method="POST">
            <input type="hidden" name="session_code" value="${logoutConfirm.code}">
            <input class="sf-btn" name="confirmLogout" id="kc-logout" type="submit" value="${msg("doLogout")}"/>
        </form>
    </#if>
</@layout.registrationLayout>

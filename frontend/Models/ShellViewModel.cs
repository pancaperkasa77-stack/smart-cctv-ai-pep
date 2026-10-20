namespace MvcSmartCctv.Models
{
    // The real app derives header/sidebar state (name, role, nav visibility)
    // from the logged-in user's Keycloak JWT (see shell.js: Shell.mount).
    // This mockup has its own dummy session-based login (AccountController +
    // Session["User"] as SessionUser, no real backend/Keycloak) — every
    // construction site now fills these in from the logged-in SessionUser
    // (see BaseController.SetShell / AdminController.Index), so there are
    // no hardcoded defaults here anymore.
    public class ShellViewModel
    {
        public string Active; // nav key: dashboard|apd|vehicle|cases|reports|settings|admin
        public string UserName;
        public string RoleLabel;
        public bool IsAdmin;
    }
}

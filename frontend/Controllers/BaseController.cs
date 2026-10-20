using System.Web.Mvc;
using MvcSmartCctv.Models;

namespace MvcSmartCctv.Controllers
{
    // Every page controller inherits this so ViewBag.Shell is always set
    // from the logged-in user, and so every action is gated behind the
    // dummy session login (see AccountController) — no [Authorize]/
    // FormsAuthentication, just a plain Session["User"] check, consistent
    // with "visual mockup, no real backend" scope.
    public abstract class BaseController : Controller
    {
        protected SessionUser CurrentUser
        {
            get { return Session[SessionKeys.User] as SessionUser; }
        }

        protected override void OnActionExecuting(ActionExecutingContext filterContext)
        {
            if (CurrentUser == null)
            {
                var returnUrl = filterContext.HttpContext.Request.RawUrl;
                filterContext.Result = new RedirectResult(
                    Url.Action("Login", "Account", new { returnUrl = returnUrl }));
                return;
            }
            base.OnActionExecuting(filterContext);
        }

        // Only ever called after OnActionExecuting has confirmed
        // CurrentUser != null, so no null-check needed here.
        protected void SetShell(string active)
        {
            var u = CurrentUser;
            ViewBag.Shell = new ShellViewModel
            {
                Active = active,
                UserName = u.DisplayName,
                RoleLabel = u.IsAdmin ? "Admin" : (u.Roles.Contains("supervisor") ? "Supervisor" : "Operator"),
                IsAdmin = u.IsAdmin,
            };
        }
    }
}

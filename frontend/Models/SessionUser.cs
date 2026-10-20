using System.Collections.Generic;

namespace MvcSmartCctv.Models
{
    public class SessionUser
    {
        public string Id;
        public string Username;
        public string DisplayName;
        public List<string> Roles;
        public bool IsAdmin;
        public string AccessToken;
    }

    internal static class SessionKeys
    {
        public const string User = "User";
    }
}

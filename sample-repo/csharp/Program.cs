using System;
using Sample.Services;

namespace Sample
{
    // Console entry point.
    public class Program
    {
        public static void Main(string[] args)
        {
            IUserService svc = new UserService();
            var user = svc.GetUser(42);
            Console.WriteLine(user.Describe());
        }
    }
}

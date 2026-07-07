using System;
using System.Collections.Generic;

namespace Sample.Services
{
    public interface IUserService
    {
        UserRecord GetUser(int id);
    }

    // Record describing a user row.
    public class UserRecord
    {
        public int Id { get; set; }
        public string Name { get; set; } = "";

        public string Describe()
        {
            return $"User {Id}: {Name}";
        }
    }

    // In-memory user lookup.
    public class UserService : IUserService
    {
        private readonly Dictionary<int, UserRecord> _cache = new();

        public UserRecord GetUser(int id)
        {
            if (!_cache.TryGetValue(id, out var rec))
            {
                rec = new UserRecord { Id = id, Name = $"user-{id}" };
                _cache[id] = rec;
            }
            return rec;
        }
    }
}

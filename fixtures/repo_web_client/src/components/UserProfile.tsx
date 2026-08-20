import React, { useEffect, useState } from 'react';
import { fetchCurrentProfile, loginUser } from '../api/authClient';

export const UserProfileView: React.FC = () => {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    async function loadData() {
      try {
        const data = await fetchCurrentProfile();
        setProfile(data);
      } catch (err) {
        console.error("Profile load failed", err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) return <div>Loading user credentials...</div>;
  if (!profile) return <div>No authenticated session.</div>;

  return (
    <div className="profile-card">
      <h3>User: {profile.username}</h3>
      <span>Role: {profile.role}</span>
    </div>
  );
};
